import os
import time
import requests
from PyPDF2 import PdfMerger
from dotenv import load_dotenv
from twilio.rest import Client
import base64

# Load environment variables
load_dotenv()

# Set your fax details from environment variables
PARENT_FOLDER = 'RequestDocuments'  # Parent folder containing all subfolders with PDFs
HUMBLEFAX_API_KEY = os.getenv('HUMBLEFAX_API_KEY')
HUMBLEFAX_SECRET_KEY = os.getenv('HUMBLEFAX_SECRET_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
DIVYESH_PHONE = os.getenv('DIVYESH_PHONE')

# HumbleFax API endpoints
HUMBLEFAX_API_URL = 'https://api.humblefax.com'
TMP_FAX_ENDPOINT = '/tmpFax'
ATTACHMENT_ENDPOINT = '/tmpFax/{faxId}/attachment'
SEND_FAX_ENDPOINT = '/tmpFax/{faxId}/send'

# Timeout and retry configuration
REQUEST_TIMEOUT = 30       # seconds per request before giving up
MAX_RETRIES = 3            # number of times to retry a failed API call
RETRY_DELAY = 5            # seconds to wait between retries
BATCH_DELAY = 2            # seconds to wait between batches

# HumbleFax credentials encoded
print(f"[INIT] Access Key: {HUMBLEFAX_API_KEY}, Secret Key: {HUMBLEFAX_SECRET_KEY}")
credentials = f"{HUMBLEFAX_API_KEY}:{HUMBLEFAX_SECRET_KEY}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

headers = {
    "Authorization": f"Basic {encoded_credentials}",
    'Content-Type': 'application/json'
}

def make_request(method, url, attempt_label="request", **kwargs):
    """
    Wrapper around requests that enforces a timeout and retries on failure.
    Returns the response object, or None if all retries are exhausted.
    method: 'get', 'post', 'delete'
    """
    kwargs.setdefault('timeout', REQUEST_TIMEOUT)
    func = getattr(requests, method)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  [HTTP] {method.upper()} {url}  (attempt {attempt}/{MAX_RETRIES})")
            response = func(url, **kwargs)
            print(f"  [HTTP] Response status: {response.status_code}")
            return response
        except requests.exceptions.Timeout:
            print(f"  [TIMEOUT] {attempt_label} timed out after {REQUEST_TIMEOUT}s (attempt {attempt}/{MAX_RETRIES})")
        except requests.exceptions.ConnectionError as e:
            print(f"  [CONNECTION ERROR] {attempt_label}: {e} (attempt {attempt}/{MAX_RETRIES})")
        except requests.exceptions.RequestException as e:
            print(f"  [REQUEST ERROR] {attempt_label}: {e} (attempt {attempt}/{MAX_RETRIES})")

        if attempt < MAX_RETRIES:
            print(f"  [RETRY] Waiting {RETRY_DELAY}s before retry...")
            time.sleep(RETRY_DELAY)

    print(f"  [FAILED] {attempt_label} failed after {MAX_RETRIES} attempts. Giving up.")
    return None

def send_completion_sms(total_time_seconds, total_faxes, failed_faxes):
    """Send a text message with the total number of faxes sent and the time taken."""
    print(f"\n[SMS] Sending completion notification to {DIVYESH_PHONE}...")
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    message_body = (
        f"Fax sending complete.\n"
        f"Sent: {total_faxes}\n"
        f"Failed: {failed_faxes}\n"
        f"Time: {total_time_seconds:.2f}s"
    )

    try:
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=DIVYESH_PHONE
        )
        print(f"[SMS] Sent successfully. SID: {message.sid}")
    except Exception as e:
        print(f"[SMS] Failed to send SMS: {e}")

def merge_pdfs_in_folder(folder_path):
    """Merge PDFs in a specific order: Request, Prescription, Authorization."""
    print(f"  [PDF] Merging PDFs in: {folder_path}")
    merger = PdfMerger()

    pdf_order = ['Request', 'Prescription', 'Authorization']
    merged_count = 0

    for pdf_type in pdf_order:
        for item in os.listdir(folder_path):
            if item.endswith('.pdf') and pdf_type.lower() in item.lower():
                pdf_path = os.path.join(folder_path, item)
                print(f"  [PDF] Appending: {item}")
                merger.append(pdf_path)
                merged_count += 1

    merged_pdf_path = os.path.join(folder_path, 'merged_document.pdf')
    merger.write(merged_pdf_path)
    merger.close()
    print(f"  [PDF] Merged {merged_count} file(s) -> {merged_pdf_path}")
    return merged_pdf_path

def get_fax_and_recipient_from_txt(folder_path):
    """Retrieve fax number and recipient name from the doctor_fax.txt file."""
    fax_file_path = os.path.join(folder_path, 'doctor_fax.txt')
    try:
        with open(fax_file_path, 'r') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                fax_number = lines[0].strip()
                recipient_name = lines[1].strip()
                print(f"  [TXT] Recipient: {recipient_name}, Fax: {fax_number}")
                return fax_number, recipient_name
            else:
                print(f"  [TXT] Invalid format in {fax_file_path}, expected at least two lines.")
                return None, None
    except FileNotFoundError:
        print(f"  [TXT] File not found: {fax_file_path}, skipping this folder.")
        return None, None

def create_tmp_fax(to_number, from_number, to_name, from_name):
    """Create a new temporary fax using HumbleFax API."""
    print(f"  [FAX] Creating temp fax -> To: {to_name} ({to_number}), From: {from_name} ({from_number})")
    url = f"{HUMBLEFAX_API_URL}{TMP_FAX_ENDPOINT}"

    payload = {
        "toName": to_name,
        "fromName": from_name,
        "fromNumber": from_number,
        "recipients": [to_number],
        "resolution": "Fine",
        "pageSize": "Letter",
        "includeCoversheet": True,
        "message": "Motus Nova Documents Request",
        "companyInfo": "Motus Nova"
    }

    response = make_request('post', url, attempt_label="create_tmp_fax", json=payload, headers=headers)

    if response is None:
        print(f"  [FAX] create_tmp_fax: no response received (all retries exhausted).")
        return None

    if response.status_code == 200:
        tmp_fax_id = response.json()['data']['tmpFax']['id']
        print(f"  [FAX] Temp fax created. ID: {tmp_fax_id}")
        return tmp_fax_id
    else:
        print(f"  [FAX] create_tmp_fax failed. Status: {response.status_code}, Body: {response.text}")
        return None

def upload_attachment(fax_id, file_path):
    """Upload a PDF attachment to the HumbleFax tmp fax."""
    print(f"  [UPLOAD] Uploading attachment for fax ID {fax_id}: {file_path}")

    if not os.path.exists(file_path):
        print(f"  [UPLOAD] File does not exist: {file_path}")
        return False

    url = f"{HUMBLEFAX_API_URL}/attachment/{fax_id}"
    upload_headers = {'Authorization': f'Basic {encoded_credentials}'}

    try:
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            response = make_request(
                'post', url,
                attempt_label="upload_attachment",
                files=files,
                headers=upload_headers
            )

        if response is None:
            print(f"  [UPLOAD] upload_attachment: no response received (all retries exhausted).")
            return False

        if response.status_code == 200:
            print(f"  [UPLOAD] Attachment uploaded successfully for fax ID {fax_id}.")
            return True
        else:
            print(f"  [UPLOAD] Upload failed. Status: {response.status_code}, Body: {response.text}")
            return False

    except Exception as e:
        print(f"  [UPLOAD] Unexpected error uploading attachment: {e}")
        return False

def send_fax(fax_id):
    """Send the fax using HumbleFax API."""
    print(f"  [SEND] Sending fax ID: {fax_id}")
    url = f"{HUMBLEFAX_API_URL}{SEND_FAX_ENDPOINT.format(faxId=fax_id)}"

    response = make_request('post', url, attempt_label="send_fax", headers=headers)

    if response is None:
        print(f"  [SEND] send_fax: no response received for fax ID {fax_id}. Attempting cleanup...")
        delete_tmp_fax(fax_id)
        return False

    if response.status_code == 200:
        print(f"  [SEND] Fax ID {fax_id} sent successfully. Deleting temp fax...")
        delete_tmp_fax(fax_id)
        return True
    else:
        print(f"  [SEND] send_fax failed for ID {fax_id}. Status: {response.status_code}, Body: {response.text}")
        print(f"  [SEND] Deleting failed temp fax ID {fax_id}...")
        delete_tmp_fax(fax_id)
        return False

def get_tmp_faxes():
    """Retrieve a list of unsent temporary faxes."""
    print("[CLEANUP] Retrieving list of unsent temporary faxes...")
    url = f"{HUMBLEFAX_API_URL}/tmpFaxes"

    response = make_request('get', url, attempt_label="get_tmp_faxes", headers=headers)

    if response is None:
        print("[CLEANUP] get_tmp_faxes: no response received.")
        return []

    if response.status_code == 200:
        tmp_faxes = response.json().get('data', {}).get('tmpFaxIds', [])
        print(f"[CLEANUP] Found {len(tmp_faxes)} unsent temp fax(es).")
        return tmp_faxes
    else:
        print(f"[CLEANUP] get_tmp_faxes failed. Status: {response.status_code}, Body: {response.text}")
        return []

def delete_tmp_fax(fax_id):
    """Delete a temporary fax by its ID."""
    print(f"  [DELETE] Deleting temp fax ID: {fax_id}")
    url = f"{HUMBLEFAX_API_URL}/tmpFax/{fax_id}"

    response = make_request('delete', url, attempt_label=f"delete_tmp_fax({fax_id})", headers=headers)

    if response is None:
        print(f"  [DELETE] delete_tmp_fax: no response for fax ID {fax_id} (all retries exhausted).")
        return False

    if response.status_code == 200:
        print(f"  [DELETE] Temp fax ID {fax_id} deleted successfully.")
        return True
    else:
        print(f"  [DELETE] delete_tmp_fax failed for ID {fax_id}. Status: {response.status_code}, Body: {response.text}")
        return False

def checkandDelete():
    """Retrieve and delete all unsent temporary faxes."""
    tmp_faxes = get_tmp_faxes()
    if tmp_faxes:
        print(f"[CLEANUP] Deleting {len(tmp_faxes)} temporary fax(es)...")
        for fax_id in tmp_faxes:
            delete_tmp_fax(fax_id)
    else:
        print("[CLEANUP] No temporary faxes to delete.")


def process_folders_in_batches(batch_size=5):
    """Process faxes in batches of a given size."""
    fax_count = 0
    fail_count = 0
    fax_number = 0

    folders = [
        folder for folder in os.listdir(PARENT_FOLDER)
        if os.path.isdir(os.path.join(PARENT_FOLDER, folder))
    ]
    print(f"\n[BATCH] Found {len(folders)} top-level folder(s) to process.")

    for i in range(0, len(folders), batch_size):
        batch_folders = folders[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        print(f"\n[BATCH] --- Batch {batch_num}: folders {i+1}-{min(i+batch_size, len(folders))} ---")

        for folder in batch_folders:
            folder_path = os.path.join(PARENT_FOLDER, folder)
            print(f"\n[FOLDER] Processing: {folder_path}")

            sub_folders = [
                sf for sf in os.listdir(folder_path)
                if sf != ".DS_Store" and os.path.isdir(os.path.join(folder_path, sf))
            ]

            for sub_folder in sub_folders:
                sub_folder_path = os.path.join(PARENT_FOLDER, folder, sub_folder)
                fax_number += 1
                print(f"\n[FAX #{fax_number}] Sub-folder: {sub_folder_path}")

                # Step 1: Merge PDFs
                try:
                    merged_pdf_path = merge_pdfs_in_folder(sub_folder_path)
                except Exception as e:
                    print(f"  [ERROR] PDF merge failed for {sub_folder_path}: {e}")
                    fail_count += 1
                    continue

                # Step 2: Read fax number + recipient
                fax_dest, recipient_name = get_fax_and_recipient_from_txt(sub_folder_path)
                if not fax_dest or not recipient_name:
                    print(f"  [SKIP] Missing fax info, skipping fax #{fax_number}.")
                    fail_count += 1
                    continue

                from_number = "14048475393"
                from_name = "Motus Nova"

                # Step 3: Create temp fax
                tmp_fax_id = create_tmp_fax(fax_dest, from_number, recipient_name, from_name)
                if not tmp_fax_id:
                    print(f"  [SKIP] Could not create temp fax for fax #{fax_number}.")
                    fail_count += 1
                    continue

                # Step 4: Upload attachment
                if not upload_attachment(tmp_fax_id, merged_pdf_path):
                    print(f"  [SKIP] Attachment upload failed for fax #{fax_number}. Deleting temp fax...")
                    delete_tmp_fax(tmp_fax_id)
                    fail_count += 1
                    continue

                # Step 5: Send fax
                success = send_fax(tmp_fax_id)
                if success:
                    fax_count += 1
                    print(f"  [OK] Fax #{fax_number} sent successfully. "
                          f"Running total: {fax_count} sent, {fail_count} failed.")
                else:
                    fail_count += 1
                    print(f"  [FAIL] Fax #{fax_number} failed. "
                          f"Running total: {fax_count} sent, {fail_count} failed.")

        print(f"\n[BATCH] Batch {batch_num} complete. Sleeping {BATCH_DELAY}s...")
        time.sleep(BATCH_DELAY)

    print(f"\n[DONE] All batches processed. Total sent: {fax_count}, Total failed: {fail_count}")
    return fax_count, fail_count

def delete_folder(folder_path):
    if os.path.exists(folder_path):
        print(f"[CLEANUP] Deleting folder: {folder_path}")
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                delete_folder(item_path)
        os.rmdir(folder_path)
        print(f"[CLEANUP] Folder deleted: {folder_path}")
    else:
        print(f"[CLEANUP] Folder does not exist: {folder_path}")

def delete_file(file_path):
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"[CLEANUP] File deleted: {file_path}")
    else:
        print(f"[CLEANUP] File does not exist: {file_path}")

if __name__ == '__main__':
    print("=" * 60)
    print("[START] FaxBlaster starting up")
    print("=" * 60)

    # Step 1: Clear any leftover temp faxes
    checkandDelete()

    start_time = time.time()

    # Step 2: Process all folders
    total_faxes_sent, total_faxes_failed = process_folders_in_batches(batch_size=5)

    end_time = time.time()
    total_time_taken = end_time - start_time

    print("\n" + "=" * 60)
    print(f"[SUMMARY] Total time:   {total_time_taken:.2f} seconds")
    print(f"[SUMMARY] Faxes sent:   {total_faxes_sent}")
    print(f"[SUMMARY] Faxes failed: {total_faxes_failed}")
    print("=" * 60)

    # Step 3: Send SMS notification
    send_completion_sms(total_time_taken, total_faxes_sent, total_faxes_failed)

    # Step 4: Clean up request docs and data file
    delete_folder(PARENT_FOLDER)
    delete_file('./data.csv')

    print("[START] FaxBlaster finished.")