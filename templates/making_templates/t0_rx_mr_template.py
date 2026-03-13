import fitz
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class t0_rx_mr_template:
    def __init__(self, pdf_template):
        self.doc = fitz.open(pdf_template)

    def create_textbox(self, name, page_number, x, y, width=130, height=13, font_size=11, font="Helv"):
        page = self.doc[page_number - 1]

        rect = fitz.Rect(x, y, x + width, y + height)

        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = rect
        widget.text_fontsize = font_size
        widget.text_align = 0

        page.add_widget(widget)

    def save_pdf(self, output_path):
        self.doc.save(output_path)


def fill_and_flatten_pdf(template_path, output_path, field_data: dict):
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
    print(f"Fields filled: {filled_fields}")
    if missing_fields:
        print(f"WARNING - Fields not provided: {missing_fields}")
    doc.bake()
    doc.save(output_path, deflate=True)
    doc.close()
    print(f"Saved filled PDF to: {output_path}")


# ── Paths ──────────────────────────────────────────────────────────────────────
RAW_TEMPLATE     = "../raw_templates/T0/RXAndMRTemplate.pdf"
TEXTBOX_TEMPLATE = "../templates_with_textboxes/T0/RXAndMRTemplate.pdf"
FILLED_OUTPUT    = "./test_files/T0_RXAndMRTemplate_filled_test.pdf"

os.makedirs("./test_files", exist_ok=True)

# ── Step 1: Create template with textboxes ─────────────────────────────────────
print("Step 1: Creating template with textboxes...")
template = t0_rx_mr_template(RAW_TEMPLATE)
template.create_textbox("Date",         1, 100, 190, 100)
template.create_textbox("To",           1, 90,  219, 100)
template.create_textbox("FaxNumber",    1, 141, 248.5, 100)
template.create_textbox("MotusProduct", 1, 338, 379, 69)
template.create_textbox("PatientName",  1, 430, 379, 180)
template.create_textbox("PatientDOB",   1, 100, 394, 78)
template.create_textbox("DocName",      1, 205, 394, 200)
template.save_pdf(TEXTBOX_TEMPLATE)
print(f"Template with textboxes saved to: {TEXTBOX_TEMPLATE}")

# ── Step 2: Fill with fake data ────────────────────────────────────────────────
print("\nStep 2: Filling template with fake data...")
fake_data = {
    "Date":         "03/13/2026",
    "To":           "Dr. John Smith",
    "FaxNumber":    "8005551234",
    "MotusProduct": "Motus Hand",
    "PatientName":  "Jane Doe",
    "PatientDOB":   "01/15/1978",
    "DocName":      "Dr. John Smith"
}
fill_and_flatten_pdf(TEXTBOX_TEMPLATE, FILLED_OUTPUT, fake_data)

print(f"\nDone! Open '{FILLED_OUTPUT}' to review the output.")