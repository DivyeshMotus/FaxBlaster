import pandas as pd
import numpy as np
import sys
sys.path.append('..')

from game_db_utils import game_db_query

import psycopg2
from config import *
import datetime
import sys

def get_patients_to_fax(cursor):
    table_query = '''
    SELECT 
        s1.story_id,
        MIN(s2.created_at) AS creation_timestamp,
        c2.first_name,
        c2.last_name,
        c2.date_of_birth AS dob,
        i.product,
        c2.phone_number,
        c2.email,
        c2.street_address AS address,
        c2.city_address AS city,
        c2.state,
        c2.zip_code,
        c1.last_name AS doctor_name,
        c1.doc_fax AS doctor_fax,
        s1.status,
        i.medical_records_auth_link
    FROM story_fresh s1
    JOIN story_fresh s2
        ON s1.destination = s2.destination
        AND s2.type = 'prescriberFax'
        AND s2.status = 'faxReady'
    LEFT JOIN contacts_fresh c1
        ON s2.origin = c1.contact_id
    LEFT JOIN contacts_fresh c2
        ON s1.destination = c2.contact_id
    LEFT JOIN insurance_fresh i
        ON s1.story_id = i.insurance_id
    WHERE s1.type = 'insurance'
        AND (
            s1.status = 'needPrescriptionOnly'
            OR s1.status = 'needMedicalRecordsOnly'
            OR s1.status = 'needPrescriptionAndMedicalRecords'
        )
    GROUP BY 
        s1.story_id,
        c1.last_name,
        c1.doc_fax,
        c2.contact_id,
        c2.first_name,
        c2.last_name,
        c2.date_of_birth,
        c2.phone_number,
        c2.email,
        c2.street_address,
        c2.city_address,
        c2.state,
        c2.zip_code,
        i.product,
        i.medical_records_auth_link
    '''
    cursor.execute(table_query)
    rows = cursor.fetchall()
    column_names = [desc[0] for desc in cursor.description]
    df = pd.DataFrame(rows, columns=column_names)
    return df