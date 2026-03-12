import fitz

class patient_authorization_template:
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
        alignment=0,
        multiline=True,
    ):
        page = self.doc[page_number - 1]

        rect = fitz.Rect(x, y, x + width, y + height)

        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = rect
        widget.text_fontsize = font_size
        widget.text_align = alignment

        if multiline:
            widget.field_flags = 4096

        page.add_widget(widget)

    def save_pdf(self, output_path):
        self.doc.save(output_path)


# Instantiate the class
template = patient_authorization_template(
    "../raw_templates/patient_authorization_template.pdf"
)

# Create text boxes
template.create_textbox("PatientName", 1, 107, 274, width=230)
template.create_textbox("PatientDOB", 1, 139, 300, width=230)
template.create_textbox("Phone Number", 1, 150, 325, width=230)
template.create_textbox("Signature", 1, 185, 569, width=230)
template.create_textbox("Date", 1, 103, 627, width=230)

# Save the PDF
template.save_pdf(
    "../templates_with_textboxes/patient_authorization_template.pdf"
)