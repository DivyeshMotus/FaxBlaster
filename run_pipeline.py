"""
run_pipeline.py — per-patient FaxBlaster pipeline.

This file REPLACES the old two-stage flow:
    Old: make.py (generate all) -> sleep 5 min -> send.py (fax all, delete all)
    New: for each patient -> generate their PDFs -> fax them -> delete their folder

It does NOT change make.py or send.py. It just imports functions from them and
calls them in a per-patient loop, so the existing scripts still work standalone.

Testing note:
    The actual send_fax() call is COMMENTED OUT so we can verify the new flow
    end-to-end without faxing real doctors. The tmp fax on HumbleFax still gets
    cleaned up so nothing is left hanging.
"""

import os
import time
import shutil
import logging
import datetime
import traceback

# ---------------------------------------------------------------------------
# Logging setup — ONLY here in run_pipeline.py.
# Creates a new timestamped log file every run, e.g.:
#   logs/faxblaster-2026-04-24_09-30-00.log
# Because Python logging is process-global, all log() calls from make.py
# and send.py automatically flow into this same file too.
# ---------------------------------------------------------------------------
def _create_timestamp():
    return datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

_logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(_logs_dir, exist_ok=True)
_log_filename = os.path.join(_logs_dir, f'faxblaster-{_create_timestamp()}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(_log_filename, mode='w'),
        logging.StreamHandler()
    ]
)

def log(message):
    logging.info(message)

log(f"[PIPELINE] Log file created: {_log_filename}")

# ---------------------------------------------------------------------------
# Imports from the existing make.py (PDF generation side)
# ---------------------------------------------------------------------------
from make import (
    authenticate_services,      # Google Drive auth
    create_connection,          # Postgres connection
    generate_template_paths,    # Returns dict of template PDF paths
    patient_record_dictionary,  # Converts DB row -> patient dict
    calculate_template_number,  # Picks T0/T1/T2 based on age of case
    create_doc_folder,          # Creates RequestDocuments/<Name>_RequestDocs/doc
    generate_authorization_pdf,
    generate_request_doc,
    generate_autofilled_prescription,
    delete_folder,
    create_folder,
    PARENT_FOLDER,              # 'RequestDocuments'
)

# `get_patients_to_fax` lives in pulling_data.py
from pulling_data import get_patients_to_fax

# ---------------------------------------------------------------------------
# Imports from the existing send.py (fax-sending side)
# ---------------------------------------------------------------------------
from send import (
    checkandDelete,             # Clears any leftover temp faxes on HumbleFax
    merge_pdfs_in_folder,
    get_fax_and_recipient_from_txt,
    create_tmp_fax,
    upload_attachment,
    send_fax,
    delete_tmp_fax,
)
from send import send_completion_sms as send_fax_summary_sms


# ---------------------------------------------------------------------------
# Testing switch
# ---------------------------------------------------------------------------
# While this is True, the actual send_fax() API call is skipped.
# The temp fax is still created, the PDF is still uploaded, and the temp fax
# is still deleted — we just don't press the final "send" button.
# Flip to False ONLY after verifying the pipeline runs cleanly end-to-end.
DRY_RUN_SEND = False


# ---------------------------------------------------------------------------
# Per-patient pipeline — the heart of this file
# ---------------------------------------------------------------------------

def process_one_patient(record, record_number, template_paths, drive_service, counters):
    """
    Generate -> fax -> delete, all for a single patient.
    Returns True if the fax was sent (or dry-run succeeded), False otherwise.
    """
    patient = patient_record_dictionary(record)
    name = f"{patient['First Name']} {patient['Last Name']}"

    log(f"\n{'=' * 60}")
    log(f"[PATIENT {record_number}] {name}  |  DOB: {patient['DOB']}  |  Status: {patient['Status']}")
    log(f"{'=' * 60}")

    # --- Skip patients missing core identity fields (same rule as make.py) ---
    if (patient['First Name'] is None) or (patient['Last Name'] is None) or (patient['DOB'] is None):
        log(f"[PATIENT {record_number}] SKIPPED — missing First Name, Last Name, or DOB.")
        counters['skipped'] += 1
        return False

    patient_full_name = patient['First Name'] + patient['Last Name']
    template_index, status_index = calculate_template_number(
        patient['Timestamp'], patient['Status']
    )

    # -----------------------------------------------------------------------
    # STEP A: Generate this patient's PDFs
    # -----------------------------------------------------------------------
    patient_folder_path = None
    try:
        patient_folder_path = create_doc_folder(patient_full_name)
        log(f"[PATIENT {record_number}] Output folder: {patient_folder_path}")

        pdf_counter = 0
        pdf_counter = generate_authorization_pdf(
            patient, patient_folder_path,
            template_paths['PatientAuthorizationTemplate'],
            drive_service, pdf_counter
        )

        if status_index == 0:
            pdf_counter = generate_request_doc(
                patient, patient_folder_path,
                template_paths[f'RXTemplate_t{template_index}'], pdf_counter
            )
        elif status_index == 1:
            pdf_counter = generate_request_doc(
                patient, patient_folder_path,
                template_paths[f'MRTemplate_t{template_index}'], pdf_counter
            )
        else:
            pdf_counter = generate_request_doc(
                patient, patient_folder_path,
                template_paths[f'RXAndMRTemplate_t{template_index}'], pdf_counter
            )

        if (status_index == 0) or (status_index == 2):
            pdf_counter = generate_autofilled_prescription(
                patient, patient_folder_path,
                template_paths['AutofillPrescription'], pdf_counter
            )

        counters['pdfs'] += pdf_counter
        log(f"[PATIENT {record_number}] Generated {pdf_counter} PDF(s).")

    except Exception as e:
        log(f"[PATIENT {record_number}] ERROR during PDF generation: {e}")
        log(f"[PATIENT {record_number}] TRACEBACK:\n{traceback.format_exc()}")
        counters['failed'] += 1
        # Best-effort cleanup so we don't leave junk behind
        _safe_delete_patient_folder(patient_full_name)
        return False

    # -----------------------------------------------------------------------
    # STEP B: Merge + fax this patient
    # -----------------------------------------------------------------------
    fax_success = False
    try:
        merged_pdf_path = merge_pdfs_in_folder(patient_folder_path)

        fax_dest, recipient_name = get_fax_and_recipient_from_txt(patient_folder_path)
        if not fax_dest or not recipient_name:
            log(f"[PATIENT {record_number}] SKIP fax — missing doctor fax info.")
            counters['failed'] += 1
        else:
            from_number = "14048475393"
            from_name = "Motus Nova"

            tmp_fax_id = create_tmp_fax(fax_dest, from_number, recipient_name, from_name)
            if not tmp_fax_id:
                log(f"[PATIENT {record_number}] SKIP fax — could not create temp fax.")
                counters['failed'] += 1
            else:
                if not upload_attachment(tmp_fax_id, merged_pdf_path):
                    log(f"[PATIENT {record_number}] SKIP fax — attachment upload failed.")
                    delete_tmp_fax(tmp_fax_id)
                    counters['failed'] += 1
                else:
                    # ---------------------------------------------------
                    # THE ACTUAL FAX-SEND STEP
                    # ---------------------------------------------------
                    if DRY_RUN_SEND:
                        log(f"[PATIENT {record_number}] [DRY RUN] Skipping send_fax({tmp_fax_id}).")
                        delete_tmp_fax(tmp_fax_id)
                        fax_success = True
                        counters['dry_run_ok'] += 1
                    else:
                        fax_success = send_fax(tmp_fax_id)
                        if fax_success:
                            counters['sent'] += 1
                        else:
                            counters['failed'] += 1

    except Exception as e:
        log(f"[PATIENT {record_number}] ERROR during fax step: {e}")
        log(f"[PATIENT {record_number}] TRACEBACK:\n{traceback.format_exc()}")
        counters['failed'] += 1

    # -----------------------------------------------------------------------
    # STEP C: Delete this patient's folder, regardless of success/failure
    # -----------------------------------------------------------------------
    _safe_delete_patient_folder(patient_full_name)
    return fax_success


def _safe_delete_patient_folder(patient_full_name):
    """Remove RequestDocuments/<Name>_RequestDocs so we free space immediately."""
    outer_folder = os.path.join(PARENT_FOLDER, f"{patient_full_name}_RequestDocs")
    if os.path.exists(outer_folder):
        try:
            shutil.rmtree(outer_folder)
            log(f"[CLEANUP] Deleted patient folder: {outer_folder}")
        except Exception as e:
            log(f"[CLEANUP] Could not delete {outer_folder}: {e}")
            log(f"[CLEANUP] TRACEBACK:\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    log("=" * 60)
    log("[PIPELINE] Starting per-patient FaxBlaster pipeline")
    log(f"[PIPELINE] DRY_RUN_SEND is {'ON (no real faxes)' if DRY_RUN_SEND else 'OFF (real faxes will send)'}")
    log("=" * 60)

    start_time = time.time()

    # --- Setup: auth, DB, templates, clean workspace ---
    drive_service = authenticate_services()

    db_connection, db_cursor = create_connection()
    df = get_patients_to_fax(db_cursor)
    log(f"[PIPELINE] Total records fetched from DB: {len(df)}")
    db_cursor.close()
    db_connection.close()

    df.to_csv("all_patients_to_fax.csv", index=False)
    log("[PIPELINE] Saved patient data to all_patients_to_fax.csv")

    # Reset workspace
    delete_folder(PARENT_FOLDER)
    create_folder(PARENT_FOLDER)

    # Clear any leftover temp faxes on HumbleFax before we start
    checkandDelete()

    template_paths = generate_template_paths()

    # --- Per-patient loop ---
    counters = {
        'sent': 0,          # real faxes sent (when DRY_RUN_SEND is False)
        'dry_run_ok': 0,    # successful dry-runs
        'failed': 0,
        'skipped': 0,
        'pdfs': 0,
    }

    total_records = len(df)
    for i, record in enumerate(df.itertuples(), start=1):
        process_one_patient(record, i, template_paths, drive_service, counters)

    # --- Summary ---
    total_time_seconds = time.time() - start_time
    log("\n" + "=" * 60)
    log(f"[PIPELINE] Complete.")
    log(f"[PIPELINE] Total records        : {total_records}")
    log(f"[PIPELINE] Real faxes sent      : {counters['sent']}")
    log(f"[PIPELINE] Dry-run OK           : {counters['dry_run_ok']}")
    log(f"[PIPELINE] Failed               : {counters['failed']}")
    log(f"[PIPELINE] Skipped (bad data)   : {counters['skipped']}")
    log(f"[PIPELINE] Total PDFs generated : {counters['pdfs']}")
    log(f"[PIPELINE] Total time           : {total_time_seconds:.2f} sec ({total_time_seconds/60:.2f} min)")
    log("=" * 60)

    # SMS: reuse send.py's summary function
    total_processed = counters['sent'] + counters['dry_run_ok']
    try:
        send_fax_summary_sms(total_time_seconds, total_processed, counters['failed'])
    except Exception as e:
        log(f"[PIPELINE] SMS send failed: {e}")
        log(f"[PIPELINE] SMS TRACEBACK:\n{traceback.format_exc()}")

    # Final workspace cleanup
    delete_folder(PARENT_FOLDER)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f"[PIPELINE] FATAL CRASH: {e}")
        log(f"[PIPELINE] TRACEBACK:\n{traceback.format_exc()}")
        try:
            # -1 signals crash so SMS says "CRASHED" instead of normal summary
            send_fax_summary_sms(0, 0, -1)
        except Exception:
            pass