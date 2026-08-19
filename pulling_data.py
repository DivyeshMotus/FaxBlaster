import pandas as pd
import numpy as np
import sys
import psycopg2
from config import *
import datetime

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
        c1.contact_id as dotor_contact_id,
        c1.first_name as doctor_first_name,
        c1.last_name AS doctor_last_name,
        c1.doc_fax AS doctor_fax,
        s1.status,
        i.medical_records_auth_link,
        c2.contact_id as patient_contact_id,

        -- ----------------------------------------------------------------
        -- Eligibility-filter columns (added for fax-blast restriction).
        -- These are consumed by apply_eligibility_filter() in
        -- run_pipeline.py. Nothing else in the pipeline reads them.
        --
        -- NOTE: in this schema `created_at` is overwritten on every UPDATE
        -- (see UPDATE ... SET created_at = NOW() in the node services), so
        -- on the *_fresh tables it means LAST UPDATED, not created.
        -- True creation date therefore comes from MIN() over the `story`
        -- audit table.
        -- ----------------------------------------------------------------

        -- When the insurance claim was first created (audit table).
        -- Correlated subquery, NOT a join: joining `story` would fan out
        -- one row per audit entry before the GROUP BY.
        (SELECT MIN(st.created_at)
           FROM story st
          WHERE st.story_id = s1.story_id) AS claim_created_at,

        -- Last time the insurance story itself was touched.
        s1.created_at AS insurance_last_update,

        -- Last time the patient's contact record was touched.
        c2.created_at AS patient_contact_last_update,

        -- Last time the doctor's contact record was touched.
        c1.created_at AS doctor_contact_last_update,

        -- Most recent prescriberFax story activity for this patient.
        -- MAX (not MIN) — we want the newest activity, whereas
        -- creation_timestamp above deliberately uses MIN because
        -- calculate_template_number() needs the earliest one.
        MAX(s2.created_at) AS prescriber_fax_last_update

    FROM story_fresh s1
    JOIN story_fresh s2
        ON s1.destination = s2.destination
        AND s2.type = 'prescriberFax'
        AND s2.status IN ('faxReady','aiConfirmedFax')
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
        c1.contact_id,
        c1.first_name,
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
        -- The new non-aggregate columns (s1.created_at, c1.created_at,
        -- c2.created_at) do NOT need to be listed here: story_fresh.story_id
        -- and contacts_fresh.contact_id are primary keys and already in the
        -- GROUP BY, so Postgres resolves the rest by functional dependency.
        -- This is the same reason s1.status works without being grouped.
    '''
    cursor.execute(table_query)
    rows = cursor.fetchall()
    column_names = [desc[0] for desc in cursor.description]
    df = pd.DataFrame(rows, columns=column_names)
    return df