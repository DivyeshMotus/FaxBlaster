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
HUMBLEFAX_API_KEY = os.getenv('HUMBLEFAX_ACCESS_KEY')
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

# HumbleFax credentials encoded
print(f"Access Key: {HUMBLEFAX_API_KEY}, Secret Key: {HUMBLEFAX_SECRET_KEY}")
credentials = f"{HUMBLEFAX_API_KEY}:{HUMBLEFAX_SECRET_KEY}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

headers = {
    "Authorization": f"Basic {encoded_credentials}",
    'Content-Type': 'application/json'
}

# Twilio SMS notification function
def send_completion_sms(total_time_seconds, total_faxes):
    """Send a text message with the total number of faxes sent and the time taken."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    message_body = (f"The fax sending process is completed.\n"
                    f"Total faxes sent: {total_faxes}\n"
                    f"Total time taken: {total_time_seconds:.2f} seconds.")

    try:
        message = client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=DIVYESH_PHONE
        )
        print(f"SMS sent to {DIVYESH_PHONE}: SID {message.sid}")
    except Exception as e:
        print(f"Failed to send SMS: {e}")

def merge_pdfs_in_folder(folder_path):
    """Merge PDFs in a specific order: Request, Prescription, Authorization."""
    merger = PdfMerger()

    # Define the desired order for PDF merging
    pdf_order = ['Request', 'Prescription', 'Authorization']

    # Merge PDFs based on their order
    for pdf_type in pdf_order:
        for item in os.listdir(folder_path):
            if item.endswith('.pdf') and pdf_type.lower() in item.lower():
                pdf_path = os.path.join(folder_path, item)
                merger.append(pdf_path)

    # Save the merged PDF
    merged_pdf_path = os.path.join(folder_path, 'merged_document.pdf')
    merger.write(merged_pdf_path)
    merger.close()
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
                return fax_number, recipient_name
            else:
                print(f"Invalid format in {fax_file_path}, expected at least two lines.")
                return None, None
    except FileNotFoundError as e:
        print(f"File not found: {fax_file_path}, skipping this folder.")
        return None, None

def create_tmp_fax(to_number, from_number, to_name, from_name):
    """Create a new temporary fax using HumbleFax API."""
    url = f"{HUMBLEFAX_API_URL}{TMP_FAX_ENDPOINT}"
    
    payload = {
        "toName": to_name,
        "fromName": from_name,
        "fromNumber": "14048475393",  # Use the correct fromNumber value provided in the error message
        "recipients": [to_number],
        "resolution": "Fine",
        "pageSize": "Letter",
        "includeCoversheet": True,
        'message': 'Motus Nova Documents Request',
        'companyInfo': 'Motus Nova'
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        tmp_fax_id = response.json()['data']['tmpFax']['id']
        return tmp_fax_id
    else:
        return None

def upload_attachment(fax_id, file_path):
    """Upload a PDF attachment to the HumbleFax tmp fax."""
    url = f"{HUMBLEFAX_API_URL}/attachment/{fax_id}"
    
    # Ensure the file exists before proceeding
    if not os.path.exists(file_path):
        return False
    
    try:
        # Open the file and send it as multipart/form-data
        with open(file_path, 'rb') as f:
            files = {
                'file': (os.path.basename(file_path), f)
            }
            # Use requests to send a multipart/form-data request
            response = requests.post(url, files=files, headers={'Authorization': f'Basic {encoded_credentials}'})
            
            if response.status_code == 200:
                return True
            else:
                return False
    except Exception as e:
        return False

def send_fax(fax_id):
    """Send the fax using HumbleFax API and delete it if sending fails."""
    url = f"{HUMBLEFAX_API_URL}{SEND_FAX_ENDPOINT.format(faxId=fax_id)}"

    response = requests.post(url, headers=headers)
    if response.status_code == 200:
        delete_tmp_fax(fax_id)  # Delete temp fax after it is successfully sent
    else:
        delete_tmp_fax(fax_id)  # Delete the temporary fax if it fails to send

def get_tmp_faxes():
    """Retrieve a list of unsent temporary faxes."""
    url = f"{HUMBLEFAX_API_URL}/tmpFaxes"
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        tmp_faxes = response.json().get('data', {}).get('tmpFaxIds', [])
        return tmp_faxes
    else:
        return []

def delete_tmp_fax(fax_id):
    """Delete a temporary fax by its ID."""
    url = f"{HUMBLEFAX_API_URL}/tmpFax/{fax_id}"
    
    response = requests.delete(url, headers=headers)

def checkandDelete():
    """Retrieve and delete all unsent temporary faxes."""
    tmp_faxes = get_tmp_faxes()
    if tmp_faxes:
        print(f"Deleting {len(tmp_faxes)} temporary faxes...")
        for fax_id in tmp_faxes:
            delete_tmp_fax(fax_id)
    else:
        print("No temporary faxes to delete.")

def process_folders_in_batches(batch_size=5):
    """Process faxes in batches of a given size."""
    fax_count = 0  # Track the number of successful faxes sent
    folders = [folder for folder in os.listdir(PARENT_FOLDER) if os.path.isdir(os.path.join(PARENT_FOLDER, folder))]
    
    # Process the folders in batches of batch_size
    for i in range(0, len(folders), batch_size):
        batch_folders = folders[i:i + batch_size]
        for folder in batch_folders:
            folder_path = os.path.join(PARENT_FOLDER, folder)
            for sub_folder in os.listdir(folder_path): 
                if sub_folder == ".DS_Store":
                    continue
                sub_folder_path = os.path.join(PARENT_FOLDER, folder, sub_folder)
                # Merge PDFs in the folder in the correct order
                merged_pdf_path = merge_pdfs_in_folder(sub_folder_path)

                # Get fax number and recipient name from txt file
                fax_number, recipient_name = get_fax_and_recipient_from_txt(sub_folder_path)
                if fax_number and recipient_name:
                    from_number = "14048475393"  # Replace with your correct fax number
                    from_name = "Motus Nova"  # Adjust as necessary

                    # Create temporary fax
                    tmp_fax_id = create_tmp_fax(fax_number, from_number, recipient_name, from_name)
                    
                    if tmp_fax_id:
                        # Upload the merged PDF
                        if upload_attachment(tmp_fax_id, merged_pdf_path):
                            # Send the fax
                            send_fax(tmp_fax_id)
                            fax_count += 1
                        else:
                            # If attachment upload fails, delete the temporary fax
                            delete_tmp_fax(tmp_fax_id)
            time.sleep(2)  # Introduce a small delay if needed

    return fax_count


def delete_folder(folder_path):
    # Check if the folder exists
    if os.path.exists(folder_path):
        # Iterate through all files and folders in the directory
        for item in os.listdir(folder_path):
            item_path = os.path.join(folder_path, item)
            # If it's a file, remove it
            if os.path.isfile(item_path):
                os.remove(item_path)
            # If it's a directory, call the function recursively
            elif os.path.isdir(item_path):
                delete_folder(item_path)
        # Now that the directory is empty, remove it
        os.rmdir(folder_path)
    else:
        print("The folder does not exist")

def delete_file(file_path):
    if os.path.exists(file_path):
        os.remove(file_path)
    else:
        print("The file does not exist")

if __name__ == '__main__':
    # First, check and delete all unsent temporary faxes
    checkandDelete()

    start_time = time.time()  # Start tracking time

    # Process all folders in batches and get the total number of faxes sent
    total_faxes_sent = process_folders_in_batches(batch_size=5)

    # Calculate total time taken
    end_time = time.time()
    total_time_taken = end_time - start_time

    print(f"Total time taken: {total_time_taken:.2f} seconds")
    print(f"Total faxes sent: {total_faxes_sent}")

    # Send SMS notification with the total faxes sent and time taken
    send_completion_sms(total_time_taken, total_faxes_sent)

    # Delete the request docs folder after done sending.
    delete_folder('./RequestDocuments')
    delete_file('./data.csv')