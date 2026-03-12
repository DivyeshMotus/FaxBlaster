import fitz

class t0_rx_template:
    def __init__(self, pdf_template):
        self.doc = fitz.open(pdf_template)

    def create_textbox(self, name, page_number, x, y, width=130, height=15, font_size=12):
        page = self.doc[page_number - 1]

        rect = fitz.Rect(x, y, x + width, y + height)

        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = rect
        widget.text_fontsize = font_size

        page.add_widget(widget)

    def save_pdf(self, output_path):
        self.doc.save(output_path)

# Instantiate the class
template = t0_rx_template("../raw_templates/T0/RXTemplate.pdf")

# Create text boxes
template.create_textbox("Date", 1, 100, 188, 100)
template.create_textbox("To", 1, 90, 216, 100)
template.create_textbox("FaxNumber", 1, 141, 246, 100)
template.create_textbox("MotusProduct", 1, 217, 406, 69)
template.create_textbox("PatientName", 1, 305, 406, 200)
template.create_textbox("PatientDOB", 1, 100, 421, 78)
template.create_textbox("DocName", 1, 205, 421, 200)

# Save
template.save_pdf("../templates_with_textboxes/T0/RXTemplate.pdf")