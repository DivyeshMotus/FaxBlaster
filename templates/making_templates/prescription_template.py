import fitz
import os

class AutofilledPrescription:
    def __init__(self, pdf_template):
        self.doc = fitz.open(pdf_template)

    def create_textbox(
        self,
        name,
        page_number,
        x,
        y,
        width=130,
        height=15,
        font_size=12,
        alignment=1,
        multiline=True
    ):
        page = self.doc[page_number - 1]
        rect = fitz.Rect(x, y, x + width, y + height)
        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = rect
        widget.text_fontsize = font_size
        widget.text_font = "Times-Roman"
        widget.text_align = alignment
        widget.field_flags = 4096 if multiline else 0
        page.add_widget(widget)

    def create_checkbox(self, name, page_number, x, y, size=9):
        page = self.doc[page_number - 1]

        rect = fitz.Rect(x, y, x + size, y + size)

        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        widget.rect = rect

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
RAW_TEMPLATE     = "../raw_templates/prescription_template.pdf"
TEXTBOX_TEMPLATE = "../templates_with_textboxes/prescription_template.pdf"
FILLED_OUTPUT    = "./test_files/prescription_template_filled_test.pdf"

os.makedirs("./test_files", exist_ok=True)

# ── Step 1: Create template with textboxes ─────────────────────────────────────
print("Step 1: Creating template with textboxes...")
autofilled_prescription = AutofilledPrescription(RAW_TEMPLATE)
autofilled_prescription.create_textbox("FirstName", 1, 106, 225, 130)
autofilled_prescription.create_textbox("LastName", 1, 317, 225, 165)
autofilled_prescription.create_textbox("Address", 1, 97, 258, 260)
autofilled_prescription.create_textbox("City", 1, 399, 258, 115)
autofilled_prescription.create_textbox("State", 1, 84, 291, 115)
autofilled_prescription.create_textbox("Zipcode", 1, 227, 291, 70)
autofilled_prescription.create_textbox("Phone Number", 1, 380, 291, 130)
autofilled_prescription.create_textbox("Birth Month", 1, 390, 324, 25)
autofilled_prescription.create_textbox("Birth Day", 1, 423, 324, 25)
autofilled_prescription.create_textbox("Birth Year", 1, 456, 324, 25, font_size=10, alignment=0)
autofilled_prescription.create_textbox("Med Note", 1, 60, 650, width=495, height=65, font_size=8, alignment=0)
autofilled_prescription.create_checkbox("Foot Product", 1, 272, 575)
autofilled_prescription.create_checkbox("Hand Product", 1, 272, 590)
autofilled_prescription.create_textbox("DoctorContactID", 1, 547, 61.5, 100, font_size=7, alignment=0)
autofilled_prescription.create_textbox("OutgoingFaxNumber", 1, 560, 71.25, 100, 100, font_size=7, alignment=0)

autofilled_prescription.save_pdf(TEXTBOX_TEMPLATE)
print(f"Template with textboxes saved to: {TEXTBOX_TEMPLATE}")

# ── Step 2: Fill with fake data ────────────────────────────────────────────────
print("\nStep 2: Filling template with fake data...")
fake_data = {
    "FirstName": "Divyesh",
    "LastName": "Ved",
    "Address": "859 Spring St NW",
    "City": "Atlanta",
    "State": "GA",
    "Zipcode": "30308",
    "Phone Number": "4045551234",
    "Birth Month": "03",
    "Birth Day": "13",
    "Birth Year": "1978",
    "Med Note": 'I am ordering the Motus Hand / Foot Rehabilitation System, a robotic based neuro-rehabilitation therapy system for use at home. My patient would functionally benefit from the active assistance and neuromuscular re-education to improve their active and passive range of motion, reduce tone, and increase strength. Additionally, it would improve fine and gross motor functions to assist in eating, dressing, walking and other activities of daily living.',
    "Foot Product": "True",
    "Hand Product": "False",
    "DoctorContactID": "123456789",
    "OutgoingFaxNumber": "123456789"
}
fill_and_flatten_pdf(TEXTBOX_TEMPLATE, FILLED_OUTPUT, fake_data)

print(f"\nDone! Open '{FILLED_OUTPUT}' to review the output.")