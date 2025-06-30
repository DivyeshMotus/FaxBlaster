import os
import re
import io
import time
import shutil
import datetime
import pandas as pd
from dotenv import load_dotenv
from twilio.rest import Client
from PyPDFForm import PdfWrapper
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

SHEET_ID = os.getenv('SHEET_ID')
RANGE_NAME = os.getenv('RANGE_NAME')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.readonly']
PARENT_FOLDER = 'RequestDocuments'
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
DIVYESH_PHONE = os.getenv('DIVYESH_PHONE')

def authenticate_services():
    credentials = service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    sheets_service = build('sheets', 'v4', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)
    return sheets_service, drive_service

def read_all_data_from_sheet(sheets_service, sheet_id, range_name):
    sheet = sheets_service.spreadsheets()
    result = sheet.values().get(spreadsheetId=sheet_id, range=range_name).execute()
    values = result.get('values', [])
    return values

def add_to_dataframe(data):
    column_names = data[0]
    data = data[1:]
    return pd.DataFrame(data, columns=column_names)

def generate_template_paths():
    template_paths = {
        'PatientAuthorizationTemplate': './templates_with_textboxes/patientauthorization_template.pdf',
        'AutofillPrescription': './templates_with_textboxes/prescription_template.pdf',
        'RXTemplate_t0': './templates_with_textboxes/T0_PDFs/RXTemplate.pdf',
        'MRTemplate_t0': './templates_with_textboxes/T0_PDFs/MRTemplate.pdf',
        'RXAndMRTemplate_t0': './templates_with_textboxes/T0_PDFs/RXAndMRTemplate.pdf',
        'RXTemplate_t1': './templates_with_textboxes/T1_PDFs/RXTemplate.pdf',
        'MRTemplate_t1': './templates_with_textboxes/T1_PDFs/MRTemplate.pdf',
        'RXAndMRTemplate_t1': './templates_with_textboxes/T1_PDFs/RXAndMRTemplate.pdf',
        'RXTemplate_t2': './templates_with_textboxes/T2_PDFs/RXTemplate.pdf',
        'MRTemplate_t2': './templates_with_textboxes/T2_PDFs/MRTemplate.pdf',
        'RXAndMRTemplate_t2': './templates_with_textboxes/T2_PDFs/RXAndMRTemplate.pdf',
    }
    return template_paths

def generate_pdfs(df, template_paths, drive_service):
    total_patients = 0
    pdf_counter = 0
    template_number_dictionary = {0:0, 1:0, 2:1, 3:1, 4:2, 5:2, 6:2}
    for record in df.itertuples():
        total_patients += 1
        patient = patient_record_dictionary(record)
        patient_full_name = patient['First Name'] + patient['Last Name']
        template_number = template_number_dictionary[int(patient['Preclaim Status'][0])]
        if patient['Doc Count'] == 2:
            for doc_count in [1,2]:
                patient_folder_path = create_doc_folder(patient_full_name, doc_count)
                if patient['Consent'] == 0:
                    pdf_counter = generate_authorization_pdf(patient, patient_folder_path, template_paths['PatientAuthorizationTemplate'], pdf_counter, drive_service)
                if patient['Prescription'] == "":
                    pdf_counter = generate_autofilled_prescription(patient, patient_folder_path, template_paths['AutofillPrescription'], pdf_counter)
                if patient['Prescription'] == "" and patient['Medical Records'] == "":
                    pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXAndMRTemplate_t{template_number}'], pdf_counter, doc_count)
                elif patient['Prescription'] != "" and patient['Medical Records'] == "":
                    pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'MRTemplate_t{template_number}'], pdf_counter, doc_count)
                elif patient['Prescription'] == "" and patient['Medical Records'] != "":
                    pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXTemplate_t{template_number}'], pdf_counter, doc_count)
        else:
            patient_folder_path = create_doc_folder(patient_full_name, 1)
            if patient['Consent'] == 0:
                pdf_counter = generate_authorization_pdf(patient, patient_folder_path, template_paths['PatientAuthorizationTemplate'], pdf_counter, drive_service)
            if patient['Prescription'] == "":
                pdf_counter = generate_autofilled_prescription(patient, patient_folder_path, template_paths['AutofillPrescription'], pdf_counter)
            if patient['Prescription'] == "" and patient['Medical Records'] == "":
                pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXAndMRTemplate_t{template_number}'], pdf_counter, 1)
            elif patient['Prescription'] != "" and patient['Medical Records'] == "":
                pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'MRTemplate_t{template_number}'], pdf_counter, 1)
            elif patient['Prescription'] == "" and patient['Medical Records'] != "":
                pdf_counter = generate_request_doc(patient, patient_folder_path, template_paths[f'RXTemplate_t{template_number}'], pdf_counter, 1)
                
    return (total_patients, pdf_counter)

def patient_record_dictionary(record):
    patient = {
        'index': record[0],
        'Timestamp': record[1],
        'PatientID': record[3],
        'First Name': record[4],
        'Last Name': record[5],
        'DOB': record[6],
        'Product': record[7],
        'Phone Number': record[8],
        'Patient Email': record[9],
        'Address': record[10],
        'City': record[11],
        'State': record[12],
        'Zipcode': record[13],
        'Prim Doc Name': record[14],
        'Prim Doc Fax': record[15],
        'Sec Doc Name': record[16],
        'Sec Doc Fax': record[17],
        'Prescription': record[18],
        'Medical Records': record[19],
        'Consent': 0 if record[20] else 1,
        'AuthorizationPDFLink': record[21],
        'Preclaim Status': record[22],
        'Doc Count': 2 if record[16] != "" or record[17] != "" else 1
    }
    return patient

def create_doc_folder(patient_full_name, doc_count):
    doc_folder_path = os.path.join(PARENT_FOLDER, f"{patient_full_name}_RequestDocs", f"doc_{doc_count}")
    os.makedirs(doc_folder_path, exist_ok=True)
    os.chmod(doc_folder_path, 0o777)
    return doc_folder_path

def generate_authorization_pdf(patient, patient_master_folder_path, authorization_template, pdf_counter, drive_service):
    patient_full_name = patient['First Name'] + patient['Last Name']
    authorization_pdf_path = os.path.join(patient_master_folder_path, f'AuthorizationDocument_{patient_full_name}.pdf')
    
    if patient['AuthorizationPDFLink']:
        download_drive_file_from_link(patient['AuthorizationPDFLink'], authorization_pdf_path, drive_service)
        pdf_counter+=1
    else:
        filled = PdfWrapper(authorization_template).fill(
            {
            "PatientName": f"{patient['First Name']} {patient['Last Name']}",
            "PatientDOB": datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%m/%d/%Y'),
            "Phone Number": patient['Phone Number'],
            "Signature": f"{patient['First Name']} {patient['Last Name']}",
            "Date": datetime.datetime.now().strftime('%m/%d/%Y'),
            }
        )
        with open(authorization_pdf_path, "wb+") as output:
            output.write(filled.read())
        pdf_counter+=1
    return pdf_counter

def delete_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print("Deleted RequestDocuments folder")
    else:
        print("The RequestDocuments folder does not exist")
    
def create_folder(email_folder_path):
    os.makedirs(email_folder_path, exist_ok=True)
    os.chmod(email_folder_path, 0o777)
    print("Created blank RequestDocuments folder")
    return email_folder_path

def download_drive_file_from_link(link, output_path, drive_service):
    file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)|id=([a-zA-Z0-9_-]+)', link)
    if not file_id_match:
        raise ValueError("Invalid Google Drive link. Could not extract file ID.")
    file_id = file_id_match.group(1) or file_id_match.group(2)
    request = drive_service.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fh = io.FileIO(output_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    print(f"Downloading file to: {output_path}")
    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f"Download progress: {int(status.progress() * 100)}%")
    print("Download completed.")

def generate_autofilled_prescription(patient, patient_master_folder_path, autofilled_prescription_template, pdf_counter):
    patient_full_name = patient['First Name'] + patient['Last Name']
    hand = patient['Product'] == "Motus Hand"
    foot = patient['Product'] == "Motus Foot"

    if patient['DOB']:
        birth_month = datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%m')
        birth_day = datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%d')
        birth_year = datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%Y')
    else:
        birth_month = ''
        birth_day = ''
        birth_year = ''

    prescription_pdf_path = os.path.join(patient_master_folder_path, f'Prescription_{patient_full_name}.pdf')
    filled = PdfWrapper(autofilled_prescription_template).fill(
        {
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
            'Foot Product': foot,
            'Hand Product': hand,
            'Med Note': 'I am ordering the Motus Hand / Foot Rehabilitation System, a robotic based neuro-rehabilitation therapy system for use at home. My patient would functionally benefit from the active assistance and neuromuscular re-education to improve their active and passive range of motion, reduce tone, and increase strength. Additionally, it would improve fine and gross motor functions to assist in eating, dressing, walking and other activities of daily living.'
        }
    )
    with open(prescription_pdf_path, "wb+") as output:
        output.write(filled.read())
    pdf_counter += 1
    return pdf_counter

def generate_request_doc(patient, doc_folder_path, request_template, pdf_counter, doc_num):
    if patient['DOB'] == "":
        pass
    doc_name = ""
    if doc_num == 1:
        doc_name = patient['Prim Doc Name']
        doc_fax_number = patient['Prim Doc Fax']
    else:
        doc_name = patient['Sec Doc Name']
        doc_fax_number = patient['Sec Doc Fax']

    motus_fax = pick_fax_number(birth_day = datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%d') ,birth_month = datetime.datetime.strptime(patient['DOB'], '%m/%d/%Y').strftime('%m'))
    patient_full_name_with_space = patient['First Name'] + ' ' + patient['Last Name']
    patient_full_name = patient['First Name'] + patient['Last Name']
    request_pdf_path = os.path.join(doc_folder_path, f'Request_{patient_full_name}_doc{doc_num}.pdf')
    filled = PdfWrapper(request_template).fill(
        {
        'Date': datetime.datetime.now().strftime('%m/%d/%Y'),
        'To': doc_name,
        'FaxNumber': doc_fax_number,
        'MotusFaxNumber': motus_fax,
        'MotusProduct': patient['Product'],
        'PatientName': patient_full_name_with_space,
        'PatientDOB': patient['DOB'],
        'DocName': doc_name
        }
    )
    with open(request_pdf_path, "wb+") as output:
        output.write(filled.read())
    generate_fax_number_file(doc_name, doc_fax_number, doc_folder_path)
    pdf_counter+=1
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
    first_index = -1
    if temp_index == 0:
        first_index= 30
    else:
        first_index = (int(birth_day) % 31) - 1
    second_index = int(birth_month) % 2
    fax_number_to_use = numbers[first_index][second_index]
    return str(fax_number_to_use)

def generate_fax_number_file(doc_name, doc_fax_number, doc_folder_path):
    fax_file_path = os.path.join(doc_folder_path, 'doctor_fax.txt')
    try:
        doc_fax_number_cleaned = standardize_fax_number(doc_fax_number)
    except ValueError as e:
        return
    with open(fax_file_path, 'w') as f:
        f.write(f"{doc_fax_number_cleaned}\n")
        f.write(f"{doc_name}")
    f.close()

def standardize_fax_number(fax_number):
    cleaned_number = re.sub(r'\D', '', fax_number)
    if len(cleaned_number) == 10:
        return cleaned_number
    else:
        raise ValueError(f"Invalid fax number: {fax_number}")

def send_completion_sms(total_time_seconds, total_patients, total_pdfs):
    phone_to_send = [DIVYESH_PHONE]
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    message_body = (f"The process of generating PDFs for the Fax blast is completed.\n"
                    f"Total PDFs generated: {total_pdfs}\n"
                    f"Total time taken: {total_time_seconds / 60} minutes.\n"
                    f"Average time taken to generate PDFs per patient: {total_time_seconds / total_patients} seconds.")
    for phone_number in phone_to_send:
        try:
            message = client.messages.create(
                body=message_body,
                from_=TWILIO_PHONE_NUMBER,
                to=phone_number
            )
            print(f"SMS sent to {phone_number}: SID {message.sid}")
        except Exception as e:
            print(f"Failed to send SMS: {e}")

def main():
    start_time = time.time()
    sheets_service, drive_service = authenticate_services()
    data = read_all_data_from_sheet(sheets_service, SHEET_ID, RANGE_NAME)
    df = add_to_dataframe(data)
    print("All the data has been read and stored in a dataframe!")
    delete_folder(PARENT_FOLDER)
    create_folder(PARENT_FOLDER)
    template_paths = generate_template_paths()
    total_patients, total_pdfs = generate_pdfs(df, template_paths, drive_service)
    end_time = time.time()
    total_time_seconds = end_time - start_time
    print(f"Total time taken: {total_time_seconds} seconds")
    send_completion_sms(total_time_seconds, total_patients, total_pdfs)

if __name__ == '__main__':
    main()