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
import csv
import glob
import time
import shutil
import smtplib
import logging
import datetime
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Shared run timestamp — used by the log file AND the three bad-fax CSVs so
# they all share the exact same suffix and can be correlated later.
# ---------------------------------------------------------------------------
def _create_timestamp():
    return datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

RUN_TIMESTAMP = _create_timestamp()

# ---------------------------------------------------------------------------
# Logging setup — ONLY here in run_pipeline.py.
# Creates a new timestamped log file every run, e.g.:
#   logs/faxblaster-2026-04-24_09-30-00.log
# Because Python logging is process-global, all log() calls from make.py
# and send.py automatically flow into this same file too.
# ---------------------------------------------------------------------------
_logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(_logs_dir, exist_ok=True)
_log_filename = os.path.join(_logs_dir, f'faxblaster-{RUN_TIMESTAMP}.log')

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
# Bad-fax CSV setup.
# Three separate folders, each holding one timestamped CSV per run.
# Headers: Story ID, Doctor Contact ID, Patient Contact ID.
# Filenames share RUN_TIMESTAMP with the log file so a run can be traced
# across all four files.
# ---------------------------------------------------------------------------
_base_dir = os.path.dirname(os.path.abspath(__file__))

_doc_fax_nan_dir = os.path.join(_base_dir, 'doc_fax_nan')
_doc_fax_stop_dir = os.path.join(_base_dir, 'doc_fax_stop')
_doc_fax_others_dir = os.path.join(_base_dir, 'doc_fax_others')
# Fourth folder: patients removed by the eligibility filter (stale claims).
# NOTE this one is different in kind from the three above — those record a
# patient but still fax them; this one records patients that were REMOVED
# from the run and never faxed.
_doc_fax_suppressed_dir = os.path.join(_base_dir, 'doc_fax_suppressed')

for d in (_doc_fax_nan_dir, _doc_fax_stop_dir, _doc_fax_others_dir,
          _doc_fax_suppressed_dir):
    os.makedirs(d, exist_ok=True)

DOC_FAX_NAN_CSV = os.path.join(_doc_fax_nan_dir, f'doc_fax_nan_{RUN_TIMESTAMP}.csv')
DOC_FAX_STOP_CSV = os.path.join(_doc_fax_stop_dir, f'doc_fax_stop_{RUN_TIMESTAMP}.csv')
DOC_FAX_OTHERS_CSV = os.path.join(_doc_fax_others_dir, f'doc_fax_others_{RUN_TIMESTAMP}.csv')
DOC_FAX_SUPPRESSED_CSV = os.path.join(
    _doc_fax_suppressed_dir, f'doc_fax_suppressed_{RUN_TIMESTAMP}.csv')

def _init_bad_fax_csv(path):
    """Create a fresh CSV with just the header row."""
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Story ID', 'Doctor Contact ID', 'Patient Contact ID'])
    log(f"[PIPELINE] Bad-fax CSV initialized: {path}")

def _init_suppressed_csv(path):
    """Suppressed CSV has an extra 'Reason' column, so it gets its own header."""
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Story ID', 'Doctor Contact ID', 'Patient Contact ID',
                         'Reason'])
    log(f"[PIPELINE] Suppressed CSV initialized: {path}")

_init_bad_fax_csv(DOC_FAX_NAN_CSV)
_init_bad_fax_csv(DOC_FAX_STOP_CSV)
_init_bad_fax_csv(DOC_FAX_OTHERS_CSV)
_init_suppressed_csv(DOC_FAX_SUPPRESSED_CSV)

# ---------------------------------------------------------------------------
# Email config (reads from .env).
# EMAIL_USER  -> sending Gmail address
# EMAIL_PASS  -> Gmail APP PASSWORD for that address (not the normal password)
# EMAIL_TO    -> recipient address
# ---------------------------------------------------------------------------
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

def _find_latest_csv(folder):
    """
    Return the path to the NEWEST (latest) CSV in `folder` by the timestamp
    embedded in its filename (doc_fax_*_YYYY-MM-DD_HH-MM-SS.csv), which sorts
    correctly as plain text. Returns None if the folder has no CSV files.
    """
    csv_files = glob.glob(os.path.join(folder, "*.csv"))
    if not csv_files:
        return None
    # Lexical sort on filename == chronological sort, thanks to the timestamp format.
    csv_files.sort(key=os.path.basename)
    return csv_files[-1]  # newest


def email_bad_fax_csvs():
    """
    Email the LATEST bad-fax CSV from each of the three folders to EMAIL_TO.

    Called at the END of the run, so each folder's newest CSV is this run's
    file (it was filled during the patient loop). If a folder has no CSV at
    all, that attachment is skipped. If no folder has any CSV, no email is sent.
    """
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        log("[EMAIL] Skipped — EMAIL_USER / EMAIL_PASS / EMAIL_TO not all set in .env")
        return

    folder_specs = [
        (_doc_fax_nan_dir, "Missing / NA fax numbers"),
        (_doc_fax_stop_dir, "STOP fax numbers"),
        (_doc_fax_others_dir, "Other invalid fax numbers"),
        (_doc_fax_suppressed_dir, "Suppressed - stale claims (NOT faxed)"),
    ]

    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    body_lines = ["Bad-fax CSV report.", ""]
    attached_any = False
    latest_stamp = None

    for folder, label in folder_specs:
        latest_path = _find_latest_csv(folder)
        if latest_path is None:
            body_lines.append(f"- {label}: no CSV found")
            continue

        # Remember the timestamp from the filename for the subject line.
        if latest_stamp is None:
            base = os.path.basename(latest_path)
            latest_stamp = base.replace(".csv", "").split("_", 3)[-1]  # the date_time part

        try:
            with open(latest_path, "r", newline="") as f:
                rows = max(len([ln for ln in f.read().splitlines() if ln.strip()]) - 1, 0)
        except Exception:
            rows = -1
        row_text = f"{rows} row(s)" if rows >= 0 else "row count unavailable"
        body_lines.append(f"- {label}: {os.path.basename(latest_path)} ({row_text})")

        with open(latest_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="csv")
        part.add_header("Content-Disposition", "attachment",
                        filename=os.path.basename(latest_path))
        msg.attach(part)
        attached_any = True

    if not attached_any:
        log("[EMAIL] No CSVs found in any folder — nothing to send.")
        return

    msg["Subject"] = f"Bad-Fax CSV Report - {latest_stamp or 'unknown'}"
    msg.attach(MIMEText("\n".join(body_lines), "plain"))

    server = None
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        log(f"[EMAIL] Latest bad-fax CSVs emailed to {EMAIL_TO}")
    except Exception as e:
        log(f"[EMAIL] Failed to send: {e}")
        log(f"[EMAIL] TRACEBACK:\n{traceback.format_exc()}")
    finally:
        if server:
            server.quit()

# ---------------------------------------------------------------------------
# Imports from the existing make.py (PDF generation side)
# ---------------------------------------------------------------------------
import make  # imported as module so we can set its CSV-path globals below

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

# Hand the three CSV paths to make.py so its _record_bad_fax() helper can find them.
make.DOC_FAX_NAN_CSV = DOC_FAX_NAN_CSV
make.DOC_FAX_STOP_CSV = DOC_FAX_STOP_CSV
make.DOC_FAX_OTHERS_CSV = DOC_FAX_OTHERS_CSV
log("[PIPELINE] Bad-fax CSV paths injected into make module.")

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
# Fax-blast eligibility filter
# ---------------------------------------------------------------------------
# Purpose: stop faxing claims that nobody is working any more.
#
# A patient is REMOVED from the run only when EVERY one of these is true:
#   1. status is one of the 3 "need*" statuses   -> already enforced in SQL
#   2. the insurance claim was created  > 12 weeks ago
#   3. the insurance story was updated  > 6 weeks ago
#   4. the patient contact was updated  > 6 weeks ago
#   5. the doctor contact was updated   > 6 weeks ago
#   6. the prescriberFax story was updated > 6 weeks ago
#
# ANY sign of recent activity means we still send the fax.
#
# NULL handling: a missing date means "unknown", NOT "stale" — we still fax.
# This is deliberate and conservative: never silently drop a patient because
# of a gap in the data.
# ---------------------------------------------------------------------------

CLAIM_AGE_DAYS = 84    # 12 weeks — how old the claim must be
STALE_DAYS = 42        # 6 weeks  — how long since any update

# Column names produced by get_patients_to_fax(). Keep in sync with the SQL.
COL_CLAIM_CREATED = 'claim_created_at'
COL_INSURANCE_UPDATED = 'insurance_last_update'
COL_PATIENT_UPDATED = 'patient_contact_last_update'
COL_DOCTOR_UPDATED = 'doctor_contact_last_update'
COL_PRESCRIBER_FAX_UPDATED = 'prescriber_fax_last_update'


def _record_suppressed(story_id, doctor_contact_id, patient_contact_id, reason):
    """Append one row to the suppressed CSV. Never raises."""
    try:
        with open(DOC_FAX_SUPPRESSED_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                story_id if story_id is not None else '',
                doctor_contact_id if doctor_contact_id is not None else '',
                patient_contact_id if patient_contact_id is not None else '',
                reason,
            ])
    except Exception as e:
        log(f"[FILTER] WARNING — could not append to suppressed CSV: {e}")
        log(f"[FILTER] TRACEBACK:\n{traceback.format_exc()}")


def _days_since(value):
    """
    Whole days between `value` and now.
    Returns None if the value is missing or cannot be parsed — callers treat
    None as "unknown", which means the patient still gets faxed.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    ts = pd.to_datetime(value, utc=True, errors='coerce')
    if ts is None or pd.isna(ts):
        return None

    return (pd.Timestamp.now(tz='UTC') - ts).days


def _evaluate_row(row):
    """
    Decide whether one patient row should be suppressed.
    Returns (should_suppress: bool, detail: str).
    The detail string is logged either way so a run can be audited.
    """
    checks = [
        ('claim_age',      _days_since(row.get(COL_CLAIM_CREATED)),          CLAIM_AGE_DAYS),
        ('insurance',      _days_since(row.get(COL_INSURANCE_UPDATED)),      STALE_DAYS),
        ('patient_contact',_days_since(row.get(COL_PATIENT_UPDATED)),        STALE_DAYS),
        ('doctor_contact', _days_since(row.get(COL_DOCTOR_UPDATED)),         STALE_DAYS),
        ('prescriber_fax', _days_since(row.get(COL_PRESCRIBER_FAX_UPDATED)), STALE_DAYS),
    ]

    for label, days, threshold in checks:
        if days is None:
            return False, f"kept: {label} date missing (unknown -> still fax)"
        if days <= threshold:
            return False, f"kept: {label} updated {days}d ago (<= {threshold}d)"

    detail = ', '.join(f"{label}={days}d" for label, days, _ in checks)
    return True, f"suppressed: all stale ({detail})"


def apply_eligibility_filter(df):
    """
    Remove stale claims from the fax list.

    Unlike the three bad-fax CSVs (which record a patient but still fax them),
    rows dropped here are genuinely removed and never faxed.

    If the expected date columns are missing entirely — e.g. running against an
    older SQL query — the filter no-ops and logs a warning rather than
    suppressing everyone or crashing.
    """
    required = [COL_CLAIM_CREATED, COL_INSURANCE_UPDATED, COL_PATIENT_UPDATED,
                COL_DOCTOR_UPDATED, COL_PRESCRIBER_FAX_UPDATED]
    missing = [c for c in required if c not in df.columns]
    if missing:
        log(f"[FILTER] SKIPPED — query is missing column(s): {missing}")
        log("[FILTER] No patients suppressed. Update pulling_data.py to add them.")
        return df

    if len(df) == 0:
        log("[FILTER] No records to filter.")
        return df

    kept_rows = []
    suppressed = 0

    for row in df.to_dict('records'):
        story_id = row.get('story_id')
        try:
            should_suppress, detail = _evaluate_row(row)
        except Exception as e:
            # Never let a filter bug drop a patient — fail open (still fax).
            log(f"[FILTER] ERROR evaluating story_id={story_id}: {e} — keeping patient.")
            log(f"[FILTER] TRACEBACK:\n{traceback.format_exc()}")
            kept_rows.append(row)
            continue

        if should_suppress:
            suppressed += 1
            log(f"[FILTER] SUPPRESS story_id={story_id} | {detail}")
            _record_suppressed(
                story_id,
                row.get('dotor_contact_id'),    # column name has the original typo
                row.get('patient_contact_id'),
                detail,
            )
        else:
            kept_rows.append(row)

    log("=" * 60)
    log(f"[FILTER] Records in        : {len(df)}")
    log(f"[FILTER] Suppressed (stale): {suppressed}")
    log(f"[FILTER] Remaining to fax  : {len(kept_rows)}")
    log(f"[FILTER] Thresholds        : claim > {CLAIM_AGE_DAYS}d, updates > {STALE_DAYS}d")
    log("=" * 60)

    return pd.DataFrame(kept_rows, columns=df.columns)


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

    # Full unfiltered pull, kept for auditing what the filter removed.
    df.to_csv("all_patients_to_fax_unfiltered.csv", index=False)
    log("[PIPELINE] Saved unfiltered patient data to all_patients_to_fax_unfiltered.csv")

    # --- Eligibility filter: drop stale claims before anything is generated ---
    df = apply_eligibility_filter(df)

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
    log(f"[PIPELINE] Records to process after eligibility filter: {total_records}")
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

    # Email the latest bad-fax CSV from each folder (this run's files, since
    # this runs after the patient loop has finished filling them).
    try:
        email_bad_fax_csvs()
    except Exception as e:
        log(f"[PIPELINE] Email send failed: {e}")
        log(f"[PIPELINE] Email TRACEBACK:\n{traceback.format_exc()}")

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