import fitz

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


autofilled_prescription = AutofilledPrescription(
    "../raw_templates/prescription_template.pdf"
)

autofilled_prescription.create_textbox("FirstName", 1, 106, 225, 130)
autofilled_prescription.create_textbox("LastName", 1, 317, 225, 165)
autofilled_prescription.create_textbox("Address", 1, 97, 258, 260)
autofilled_prescription.create_textbox("City", 1, 399, 258, 115)
autofilled_prescription.create_textbox("State", 1, 84, 291, 115)
autofilled_prescription.create_textbox("Zipcode", 1, 227, 291, 70)
autofilled_prescription.create_textbox("Phone Number", 1, 380, 291, 130)
autofilled_prescription.create_textbox("Birth Month", 1, 390, 324, 25)
autofilled_prescription.create_textbox("Birth Day", 1, 423, 324, 25)
autofilled_prescription.create_textbox("Birth Year", 1, 456, 324, 25)
autofilled_prescription.create_textbox("Med Note", 1, 60, 650, width=495, height=65, font_size=8, alignment=0)
autofilled_prescription.create_checkbox("Foot Product", 1, 272, 575)
autofilled_prescription.create_checkbox("Hand Product", 1, 272, 590)

autofilled_prescription.save_pdf(
    "../templates_with_textboxes/prescription_template.pdf"
)