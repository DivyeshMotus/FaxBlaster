import requests
import base64
import dotenv
import os
import pandas as pd
from datetime import datetime, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Load environment variables from .env file
dotenv.load_dotenv()

def get_credentials():
    access_key = os.getenv("HUMBLEFAX_ACCESS_KEY")
    secret_key = os.getenv("HUMBLEFAX_SECRET_KEY")
    user_email = os.getenv("USER_EMAIL")
    user_password = os.getenv("USER_PASSWORD")
    return access_key, secret_key, user_email, user_password

def encode_credentials(access_key, secret_key):
    credentials = f"{access_key}:{secret_key}"
    return base64.b64encode(credentials.encode()).decode()

def fetch_sent_faxes(start_time, end_time, from_number, headers):
    url = "https://api.humblefax.com/sentFaxes"
    params = {
        "timeFrom": str(int(start_time)),  # Start timestamp
        "timeTo": str(int(end_time)),      # End timestamp
        "fromNumber": from_number
    }
    response = requests.get(url, params=params, headers=headers)
    return response

def fetch_fax_details(fax_id, headers):
    url = f'https://api.humblefax.com/sentFax/{fax_id}'
    response = requests.get(url, headers=headers)
    return response

def collect_failed_faxes(response_json, headers):
    df = pd.DataFrame(columns=['faxID', 'faxNumber', 'reasonForFailure'])
    number_of_failed_faxes = 0
    
    for fax_id in response_json['data']['sentFaxIds']:
        fax_response = fetch_fax_details(fax_id, headers)
        if fax_response.status_code == 200:
            fax_json = fax_response.json()
            if fax_json['data']['sentFax']['status'] == 'failure':
                number_of_failed_faxes += 1
                df.loc[len(df)] = [
                    fax_json['data']['sentFax']['id'],
                    fax_json['data']['sentFax']['recipients'][0]['toNumber'][1:],  # Remove leading + sign
                    fax_json['data']['sentFax']['recipients'][0]['failureReason']
                ]
    return df, number_of_failed_faxes

def dataframe_to_email_body(dataframe):
    if dataframe.empty:
        return "There are no failed faxes for the specified date."
    
    email_body = "List of Failed Faxes:\n\n"
    email_body += dataframe.to_string(index=False, header=True)
    return email_body

def send_email(to_address, subject, body, smtp_server, smtp_port, smtp_user, smtp_password):
    for address in to_address:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = address
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        server = None
        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()  # Secure the connection
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            print(f"Email sent successfully to {address}")
        except Exception as e:
            print(f"Error sending email: {e}")
        finally:
            if server:
                server.quit()

def authenticate_services(credentials_file, scopes):
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=scopes
    )
    return build('sheets', 'v4', credentials=credentials)

def update_google_sheet(dataframe, spreadsheet_id, sheet_name, sheets_service):
    try:
        sheets_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_names = [sheet['properties']['title'] for sheet in sheets_metadata.get('sheets', [])]

        if sheet_name not in sheet_names:
            body = {'requests': [{'addSheet': {'properties': {'title': sheet_name}}}]}
            sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
            print(f"Sheet '{sheet_name}' created.")
        else:
            print(f"Sheet '{sheet_name}' already exists.")
        
        data = [dataframe.columns.values.tolist()] + dataframe.values.tolist()
        range_name = f"{sheet_name}!A1"
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption='RAW',
            body={'values': data}
        ).execute()
        print("Data successfully updated in Google Sheet.")
    except Exception as e:
        print(f"Error updating Google Sheet: {e}")

def main():
    # Retrieve credentials
    access_key, secret_key, user_email, user_password = get_credentials()
    encoded_credentials = encode_credentials(access_key, secret_key)

    headers = {"Authorization": f"Basic {encoded_credentials}"}

    # Get today's date in UTC
    today = datetime.now(timezone.utc)
    start_time = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    end_time = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    today_date_formatted = today.strftime("%m/%d/%Y")
    # Fetch sent faxes
    response = fetch_sent_faxes(start_time, end_time, "14048475393", headers)
    
    if response.status_code == 200:
        response_json = response.json()
        df, number_of_failed_faxes = collect_failed_faxes(response_json, headers)
        print(f"{number_of_failed_faxes} faxes failed")

        # Convert the DataFrame to a formatted email body
        email_body = dataframe_to_email_body(df)

        # Email details
        to_address = ["divyesh.ved@motusnova.com"]
        subject = f"List of Failed Faxes - {today_date_formatted}"

        # SMTP server details
        smtp_server = "smtp.gmail.com"  # Replace with your SMTP server
        smtp_port = 587  # Port for TLS
        smtp_user = user_email  # Replace with your email
        smtp_password = user_password  # Replace with your email password

        # Send the email
        send_email(to_address, subject, email_body, smtp_server, smtp_port, smtp_user, smtp_password)

        # Authenticate Google Sheets service
        sheets_service = authenticate_services('credentials.json', ['https://www.googleapis.com/auth/spreadsheets'])

        # Update Google Sheet
        spreadsheet_id = '1zUvUpPN8uJnMTIO_rM0vrIAcXHO9_VOTtCygIjubMo4'
        sheet_name = f'{today_date_formatted}'
        update_google_sheet(df, spreadsheet_id, sheet_name, sheets_service)
    else:
        print(f"Failed to get sent faxes: {response.status_code} - {response.text}")

if __name__ == "__main__":
    main()