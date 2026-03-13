import os
import re
import io
import fitz
import time
import shutil
import logging
import datetime
import requests
import magic
import img2pdf
import pillow_heif
import psycopg2
import pandas as pd
from PIL import Image
from urllib.parse import urlparse
from dotenv import load_dotenv
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pulling_data import *
from config import *

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler('faxblaster.log', mode='w'),
        logging.StreamHandler()
    ]
)

def log(message):
    logging.info(message)

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
PARENT_FOLDER = 'RequestDocuments'
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
DIVYESH_PHONE = os.getenv('DIVYESH_PHONE')
NA_PATTERNS = {"#na", "#n/a", "na", "n/a", "none", "null", "-", "--"}

def fill_and_flatten_pdf(template_path, output_path, field_data: dict):
    log(f"[FILL PDF] Opening template: {template_path}")
    doc = fitz.open(template_path)
    filled_fields = []
    missing_fields = []
    for page in doc:
        for widget in page.widgets():
            if widget.field_name in field_data:
                widget.field_value = str(field_data[widget.field_name])
                widget.update()
                filled_fields.append(widget.field_name)
            else:
                missing_fields.append(widget.field_name)
    log(f"[FILL PDF] Fields filled: {filled_fields}")
    if missing_fields:
        log(f"[FILL PDF] WARNING - Fields in template not provided: {missing_fields}")
    doc.bake()
    doc.save(output_path, deflate=True)
    doc.close()
    log(f"[FILL PDF] Filled and flattened saved to: {output_path}")

def authenticate_services():
    log("[AUTH] Authenticating Google Drive service account...")
    credentials = service_account.Credentials.from_service_account_file('google_credentials.json', scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=credentials)
    log("[AUTH] Google Drive authentication successful.")
    return drive_service

def generate_template_paths():
    log("[TEMPLATES] Generating template paths...")
    template_paths = {
        'PatientAuthorizationTemplate': './templates/templates_with_textboxes/patient_authorization_template.pdf',
        'AutofillPrescription': './templates/templates_with_textboxes/prescription_template.pdf',
        'RXTemplate_t0': './templates/templates_with_textboxes/T0/RXTemplate.pdf',
        'MRTemplate_t0': './templates/templates_with_textboxes/T0/MRTemplate.pdf',
        'RXAndMRTemplate_t0': './templates/templates_with_textboxes/T0/RXAndMRTemplate.pdf',
        'RXTemplate_t1': './templates/templates_with_textboxes/T1/RXTemplate.pdf',
        'MRTemplate_t1': './templates/templates_with_textboxes/T1/MRTemplate.pdf',
        'RXAndMRTemplate_t1': './templates/templates_with_textboxes/T1/RXAndMRTemplate.pdf',
        'RXTemplate_t2': './templates/templates_with_textboxes/T2/RXTemplate.pdf',
        'MRTemplate_t2': './templates/templates_with_textboxes/T2/MRTemplate.pdf',
        'RXAndMRTemplate_t2': './templates/templates_with_textboxes/T2/RXAndMRTemplate.pdf',
    }
    for name, path in template_paths.items():
        exists = os.path.exists(path)
        log(f"[TEMPLATES]   {name}: {path} -> {'EXISTS' if exists else 'MISSING'}")
    return template_paths

def generate_pdfs(df, template_paths, drive_service):
    total_patients = 0
    pdf_counter = 0
    log(f"\n[GENERATE] Starting PDF generation for {len(df)} records...")
    for record in df.itertuples():
        total_patients += 1
        patient = patient_record_dictionary(record)
        log(f"\n[PATIENT {total_patients}] Processing: {patient['First Name']} {patient['Last Name']} | DOB: {patient['DOB']} | Status: {patient['Status']}")

        if (patient['First Name'] is None) or (patient['Last Name'] is None) or (patient['DOB'] is None):
            log(f"[PATIENT {total_patients}] SKIPPED — missing First Name, Last Name, or DOB.")
            continue

        patient_full_name = patient['First Name'] + patient['Last Name']
        template_index, status_index = calculate_template_number(patient['Timestamp'], patient['Status'])
        log(f"[PATIENT {total_patients}] Template index: t{template_index} | Status index: {status_index}")

        patient_folder_path = create_doc_folder(patient_full_name)
        log(f"[PATIENT {total_patients}] Output folder: {patient_folder_path}")

        pdf_counter = generate_authorization_pdf(patient, patient_folder_path, template_paths['PatientAuthorizationTemplate'], drive_service, pdf_counter)

        if status_index == 0:
            log(f"[PATIENT {total_patients}] Generating RX (prescription request) doc...")
            pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXTemplate_t{template_index}'], pdf_counter)
        elif status_index == 1:
            log(f"[PATIENT {total_patients}] Generating MR (medical records request) doc...")
            pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'MRTemplate_t{template_index}'], pdf_counter)
        else:
            log(f"[PATIENT {total_patients}] Generating RX+MR (both) doc...")
            pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXAndMRTemplate_t{template_index}'], pdf_counter)

        if (status_index == 0) or (status_index == 2):
            log(f"[PATIENT {total_patients}] Generating autofilled prescription...")
            pdf_counter = generate_autofilled_prescription(patient, patient_folder_path, template_paths['AutofillPrescription'], pdf_counter)

        log(f"[PATIENT {total_patients}] Done. Running PDF total: {pdf_counter}")

    log(f"\n[GENERATE] PDF generation complete. Total patients: {total_patients} | Total PDFs: {pdf_counter}")
    return (total_patients, pdf_counter)

def patient_record_dictionary(record):
    raw_fax = str(record[15]) if record[15] is not None else None
    try:
        cleaned_fax = standardize_fax_number(raw_fax)
    except ValueError as e:
        log(f"[PATIENT RECORD] WARNING — invalid fax number for record {record[1]}: '{raw_fax}' | {e}")
        cleaned_fax = None

    patient = {
        'story_id': record[1],
        'Timestamp': record[2],
        'First Name': record[3],
        'Last Name': record[4],
        'DOB': record[5],
        'Product': record[6],
        'Phone Number': record[7],
        'Patient Email': record[8],
        'Address': record[9],
        'City': record[10],
        'State': record[11],
        'Zipcode': record[12],
        'Prim Doc First Name': record[13],
        'Prim Doc Last Name': record[14],
        'Prim Doc Fax': cleaned_fax,
        'Status': record[16],
        'AuthorizationPDFLink': record[17]
    }
    return patient

def calculate_template_number(creation_timestamp, status):
    if hasattr(creation_timestamp, 'to_pydatetime'):
        creation_date = creation_timestamp.to_pydatetime()
        if creation_date.tzinfo is not None:
            creation_date = creation_date.replace(tzinfo=None)
    else:
        creation_date = datetime.datetime.strptime(creation_timestamp, '%m/%d/%Y')

    today = datetime.datetime.now()
    days_difference = (today - creation_date).days
    log(f"[TEMPLATE CALC] Creation date: {creation_date.strftime('%m/%d/%Y')} | Days since creation: {days_difference}")

    template_index = -1
    status_index = -1

    if (days_difference > 0) and (days_difference <= 14):
        template_index = 0
    elif (days_difference > 15) and (days_difference <= 28):
        template_index = 1
    else:
        template_index = 2

    if status == "needPrescriptionOnly":
        status_index = 0
    elif status == "needMedicalRecordsOnly":
        status_index = 1
    else:
        status_index = 2

    log(f"[TEMPLATE CALC] Resolved -> template_index: {template_index} | status_index: {status_index}")
    return (template_index, status_index)

def create_doc_folder(patient_full_name):
    base_folder_path = os.path.join(PARENT_FOLDER, f"{patient_full_name}_RequestDocs")
    doc_folder_path = os.path.join(base_folder_path, "doc")
    counter = 1
    while os.path.exists(doc_folder_path):
        counter += 1
        doc_folder_path = os.path.join(base_folder_path, f"doc{counter}")

    os.makedirs(doc_folder_path, exist_ok=True)
    os.chmod(doc_folder_path, 0o777)
    log(f"[FOLDER] Created folder: {doc_folder_path}")
    return doc_folder_path

def download_drive_file_from_link(link, output_path, drive_service):
    log(f"[DRIVE DOWNLOAD] Extracting file ID from link: {link}")
    file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)|id=([a-zA-Z0-9_-]+)', link)
    if not file_id_match:
        raise ValueError("Invalid Google Drive link. Could not extract file ID.")
    file_id = file_id_match.group(1) or file_id_match.group(2)
    log(f"[DRIVE DOWNLOAD] File ID: {file_id}")
    request = drive_service.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fh = io.FileIO(output_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    log(f"[DRIVE DOWNLOAD] Downloading to: {output_path}")
    done = False
    while not done:
        status, done = downloader.next_chunk()
        log(f"[DRIVE DOWNLOAD] Progress: {int(status.progress() * 100)}%")
    log(f"[DRIVE DOWNLOAD] Download complete: {output_path}")

def download_aws_file_from_link(link, output_path):
    log(f"[AWS DOWNLOAD] Fetching from S3: {link}")
    pillow_heif.register_heif_opener()
    ALLOWED_IMAGE_MIME_PREFIX = "image/"
    response = requests.get(link)
    response.raise_for_status()
    content = response.content
    mime = magic.from_buffer(content, mime=True)
    log(f"[AWS DOWNLOAD] Detected MIME type: {mime}")
    if mime == "application/pdf":
        with open(output_path, "wb") as f:
            f.write(content)
        log(f"[AWS DOWNLOAD] Saved PDF -> {output_path}")
        return output_path
    elif mime.startswith(ALLOWED_IMAGE_MIME_PREFIX):
        temp_img = output_path + ".img"
        with open(temp_img, "wb") as f:
            f.write(content)
        log(f"[AWS DOWNLOAD] Image saved to temp: {temp_img}, converting to PDF...")
        try:
            with open(output_path, "wb") as f:
                f.write(img2pdf.convert(temp_img))
            log(f"[AWS DOWNLOAD] img2pdf conversion successful -> {output_path}")
        except Exception as e:
            log(f"[AWS DOWNLOAD] img2pdf failed ({e}), falling back to Pillow...")
            image = Image.open(temp_img)
            image.convert("RGB").save(output_path)
            log(f"[AWS DOWNLOAD] Pillow fallback conversion successful -> {output_path}")
        return output_path
    elif mime == "text/html":
        raise Exception("S3 returned HTML instead of file (likely permission issue)")
    else:
        raise Exception(f"Unsupported file type: {mime}")

def classify_link(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if "amazonaws.com" in netloc:
        log(f"[CLASSIFY LINK] Identified as AWS S3: {url}")
        return "aws_s3"
    elif "drive.google.com" in netloc or "docs.google.com" in netloc:
        log(f"[CLASSIFY LINK] Identified as Google Drive: {url}")
        return "google_drive"
    else:
        log(f"[CLASSIFY LINK] Unknown link type for: {url}")
        return None



def generate_name(first_name, last_name):
    first_name = first_name if isinstance(first_name, str) else ""
    last_name = last_name if isinstance(last_name, str) else ""

    # Treat placeholder values as empty
    if first_name.strip().lower() in NA_PATTERNS:
        first_name = ""
    if last_name.strip().lower() in NA_PATTERNS:
        last_name = ""
    first = " ".join(word.capitalize() for word in first_name.strip().split())
    last = " ".join(word.capitalize() for word in last_name.strip().split())
    return f"{first} {last}".strip()

def generate_authorization_pdf(patient, folder, template_path, drive_service, pdf_counter):
    path = os.path.join(folder, f"AuthorizationDocument_{patient['First Name']}{patient['Last Name']}.pdf")
    patient_name = generate_name(patient['First Name'], patient['Last Name'])
    log(f"[AUTH PDF] Generating authorization PDF for: {patient_name}")
    link = patient['AuthorizationPDFLink']
    has_link = link is not None and isinstance(link, str) and link.strip() != ''

    if has_link:
        log(f"[AUTH PDF] Authorization link found: {link}")
        link_type = classify_link(link)
        if link_type == 'aws_s3':
            download_aws_file_from_link(link, path)
        elif link_type == 'google_drive':
            download_drive_file_from_link(link, path, drive_service)
        else:
            log(f"[AUTH PDF] WARNING: Unknown link type, skipping download.")
    else:
        log(f"[AUTH PDF] No link found — filling template: {template_path}")
        fill_and_flatten_pdf(template_path, path, {
            'patient_name': patient_name,
            'patient_dob': patient['DOB'].strftime('%m/%d/%Y'),
            'patient_phone': patient['Phone Number'],
            'patient_name_2': patient_name,
            'date': datetime.datetime.now().strftime('%m/%d/%Y')
        })
        log(f"[AUTH PDF] Template-filled authorization saved to: {path}")

    pdf_counter += 1
    log(f"[AUTH PDF] Done. PDF counter now: {pdf_counter}")
    return pdf_counter

def delete_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        log(f"[FOLDER] Deleted folder: {folder_path}")
    else:
        log(f"[FOLDER] Folder does not exist, skipping delete: {folder_path}")

def create_folder(email_folder_path):
    os.makedirs(email_folder_path, exist_ok=True)
    os.chmod(email_folder_path, 0o777)
    log(f"[FOLDER] Created blank folder: {email_folder_path}")
    return email_folder_path

def generate_autofilled_prescription(patient, patient_master_folder_path, autofilled_prescription_template, pdf_counter):
    patient_full_name = patient['First Name'] + patient['Last Name']
    hand = patient['Product'] == "Motus Hand"
    foot = patient['Product'] == "Motus Foot"
    log(f"[PRESCRIPTION] Generating prescription for: {patient_full_name} | Product: {patient['Product']} | Hand: {hand} | Foot: {foot}")

    if patient['DOB']:
        birth_month = patient['DOB'].strftime('%m')
        birth_day = patient['DOB'].strftime('%d')
        birth_year = patient['DOB'].strftime('%Y')
    else:
        birth_month = ''
        birth_day = ''
        birth_year = ''
        log(f"[PRESCRIPTION] WARNING: DOB missing for {patient_full_name}, birth fields will be blank.")

    prescription_pdf_path = os.path.join(patient_master_folder_path, f'Prescription_{patient_full_name}.pdf')
    log(f"[PRESCRIPTION] Filling template: {autofilled_prescription_template}")

    fill_and_flatten_pdf(autofilled_prescription_template, prescription_pdf_path, {
        'FirstName': patient['First Name'],
        'LastName': patient['Last Name'],
        'Address': str(patient['Address']).strip(),
        'City': patient['City'],
        'State': patient['State'],
        'Zipcode': patient['Zipcode'],
        'Phone Number': patient['Phone Number'],
        'Birth Month': birth_month,
        'Birth Day': birth_day,
        'Birth Year': birth_year,
        'Foot Product': "Yes" if foot else "Off",
        'Hand Product': "Yes" if hand else "Off",
        'Med Note': 'I am ordering the Motus Hand / Foot Rehabilitation System, a robotic based neuro-rehabilitation therapy system for use at home. My patient would functionally benefit from the active assistance and neuromuscular re-education to improve their active and passive range of motion, reduce tone, and increase strength. Additionally, it would improve fine and gross motor functions to assist in eating, dressing, walking and other activities of daily living.'
    })

    pdf_counter += 1
    log(f"[PRESCRIPTION] Saved to: {prescription_pdf_path} | PDF counter now: {pdf_counter}")
    return pdf_counter

def generate_request_doc(patient, doc_folder_path, request_template, pdf_counter):
    doc_name = generate_name(patient['Prim Doc First Name'], patient['Prim Doc Last Name'])
    doc_fax_number = patient['Prim Doc Fax']
    patient_full_name_with_space = patient['First Name'] + ' ' + patient['Last Name']
    patient_full_name = patient['First Name'] + patient['Last Name']

    log(f"[REQUEST DOC] Generating request doc for: {patient_full_name_with_space}")
    log(f"[REQUEST DOC] Doctor: {doc_name} | Doctor fax: {doc_fax_number}")
    log(f"[REQUEST DOC] Using template: {request_template}")

    motus_fax = pick_fax_number(birth_day=patient['DOB'].strftime('%d'), birth_month=patient['DOB'].strftime('%m'))
    log(f"[REQUEST DOC] Assigned Motus fax number: {motus_fax}")

    request_pdf_path = os.path.join(doc_folder_path, f'Request_{patient_full_name}_doc.pdf')

    fill_and_flatten_pdf(request_template, request_pdf_path, {
        'Date': datetime.datetime.now().strftime('%m/%d/%Y'),
        'To': doc_name,
        'FaxNumber': doc_fax_number,
        'MotusFaxNumber': motus_fax,
        'MotusProduct': patient['Product'],
        'PatientName': patient_full_name_with_space,
        'PatientDOB': patient['DOB'].strftime('%m/%d/%Y'),
        'DocName': doc_name
    })

    log(f"[REQUEST DOC] Saved to: {request_pdf_path}")
    generate_fax_number_file(doc_name, doc_fax_number, doc_folder_path)
    pdf_counter += 1
    log(f"[REQUEST DOC] Done. PDF counter now: {pdf_counter}")
    return pdf_counter

def pick_fax_number(birth_day, birth_month):
    numbers = [
        [5414078870, 4173863088],
        [4177385090, 5418032963],
        [5418033064, 5414135726],
        [4173612814, 5414223358],
        [5418033298, 4173863480],
        [5418033051, 4177385061],
        [2184293566, 4173863058],
        [5414223357, 5418033142],
        [2184383078, 4173863059],
        [5414073925, 4173863175],
        [5414135731, 5418032645],
        [4173863121, 5414135790],
        [5414223362, 5414073957],
        [4177385102, 5414223139],
        [5414223354, 5418032629],
        [4173863194, 5414223452],
        [5414073934, 5418032650],
        [5414073956, 4177383964],
        [4173862613, 5414135749],
        [5414073973, 4173862607],
        [5414223183, 4173862623],
        [5414073929, 5413736201],
        [5414073982, 4177385004],
        [4177383942, 4173863117],
        [5418032624, 4177383408],
        [7752978471, 7756186914],
        [7752977579, 7755085077],
        [7755085114, 7752977559],
        [7756186529, 7752977671],
        [7756186931, 7752977537],
        [7755085530, 7756186411]
    ]
    temp_index = int(birth_day) % 31
    first_index = 30 if temp_index == 0 else (int(birth_day) % 31) - 1
    second_index = int(birth_month) % 2
    fax_number_to_use = numbers[first_index][second_index]
    log(f"[FAX PICKER] birth_day={birth_day}, birth_month={birth_month} -> first_index={first_index}, second_index={second_index} -> fax={fax_number_to_use}")
    return str(fax_number_to_use)

def generate_fax_number_file(doc_name, doc_fax_number, doc_folder_path):
    fax_file_path = os.path.join(doc_folder_path, 'doctor_fax.txt')
    log(f"[FAX FILE] Writing doctor_fax.txt for: {doc_name} | Raw fax number: {doc_fax_number}")
    try:
        doc_fax_number_cleaned = standardize_fax_number(doc_fax_number)
    except ValueError as e:
        log(f"[FAX FILE] WARNING: Skipping fax file — {e}")
        return
    with open(fax_file_path, 'w') as f:
        f.write(f"{doc_fax_number_cleaned}\n")
        f.write(f"{doc_name}")
    log(f"[FAX FILE] Written -> Line 1 (fax): {doc_fax_number_cleaned} | Line 2 (doctor): {doc_name}")
    log(f"[FAX FILE] Saved to: {fax_file_path}")

def standardize_fax_number(fax_number):
    if fax_number is None or not isinstance(fax_number, str) or fax_number.strip() == '':
        raise ValueError(f"Invalid fax number: {fax_number}")

    # Strip trailing .0 caused by pandas/DB storing numbers as floats
    fax_number = re.sub(r'\.0+$', '', fax_number.strip())

    cleaned_number = re.sub(r'\D', '', fax_number)

    # Strip leading country code 1 or +1
    if len(cleaned_number) == 11 and cleaned_number.startswith('1'):
        cleaned_number = cleaned_number[1:]

    if len(cleaned_number) == 10:
        log(f"[FAX STANDARDIZE] '{fax_number}' -> '{cleaned_number}'")
        return cleaned_number
    else:
        raise ValueError(f"Invalid fax number (not 10 digits after cleaning): '{fax_number}' -> '{cleaned_number}'")

def send_completion_sms(total_time_seconds, total_patients, total_pdfs):
    log(f"\n[SMS] Sending completion SMS to {DIVYESH_PHONE}...")
    phone_to_send = [DIVYESH_PHONE]
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    message_body = (f"The process of generating PDFs for the Fax blast is completed\n"
                    f"Total PDFs generated: {total_pdfs}\n"
                    f"Total Patients Faxed: {total_patients}\n"
                    f"Total time taken: {total_time_seconds / 60:.2f} minutes\n"
                    f"Average time taken to generate PDFs per patient: {total_time_seconds / total_patients:.2f} seconds")
    for phone_number in phone_to_send:
        try:
            message = client.messages.create(
                body=message_body,
                from_=TWILIO_PHONE_NUMBER,
                to=phone_number
            )
            log(f"[SMS] Sent to {phone_number}: SID {message.sid}")
        except Exception as e:
            log(f"[SMS] Failed to send to {phone_number}: {e}")

def create_connection():
    log("[DB] Creating database connection...")
    params = game_db_query.game_db_config()
    game_db_conn = psycopg2.connect(**params)
    game_db_cur = game_db_conn.cursor()
    log("[DB] Connection established.")
    return game_db_conn, game_db_cur

def main():
    log("=" * 60)
    log("[MAIN] Starting FaxBlaster PDF generation pipeline...")
    log("=" * 60)
    start_time = time.time()

    drive_service = authenticate_services()

    db_connection, db_cursor = create_connection()
    df = get_patients_to_fax(db_cursor)
    db_cursor.close()
    db_connection.close()
    log(f"[MAIN] Data loaded. Total records fetched: {len(df)}")

    df.to_csv("all_patients_to_fax.csv", index=False)
    log("[MAIN] Saved patient data to all_patients_to_fax.csv")

    delete_folder(PARENT_FOLDER)
    create_folder(PARENT_FOLDER)

    template_paths = generate_template_paths()
    total_patients, total_pdfs = generate_pdfs(df, template_paths, drive_service)

    end_time = time.time()
    total_time_seconds = end_time - start_time

    log("\n" + "=" * 60)
    log(f"[MAIN] Pipeline complete.")
    log(f"[MAIN] Total patients processed : {total_patients}")
    log(f"[MAIN] Total PDFs generated     : {total_pdfs}")
    log(f"[MAIN] Total time taken         : {total_time_seconds:.2f} seconds ({total_time_seconds / 60:.2f} minutes)")
    log("=" * 60)

    send_completion_sms(total_time_seconds, total_patients, total_pdfs)

if __name__ == '__main__':
    main()
