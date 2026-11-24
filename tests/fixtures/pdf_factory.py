"""Test helpers and ReportLab PDF generators matching real back-office reports."""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas


def build_sales_and_labor_pdf() -> bytes:
    """Generate a realistic borderless 'Sales and Labor Report by Hour' PDF matching Image 2."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    # Top header
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30, height - 35, "Sales and Labor Report by Hour")
    c.drawRightString(width - 30, height - 35, "All DQ Locations")

    c.setFont("Helvetica", 8.5)
    c.drawString(30, height - 50, "11/24/2026 2:15:04 PM (UTC-05:00) Eastern Time (US & Canada)")
    c.drawRightString(width - 30, height - 50, "Tuesday, November 24, 2026")

    c.setStrokeColor(colors.black)
    c.line(25, height - 60, width - 25, height - 60)

    cols_x = [30, 125, 185, 255, 320, 390, 455, 515, 575]
    headers = [
        "Hour",
        "Net Sales",
        "Guests",
        "Guest Average",
        "Orders",
        "Order Average",
        "Labor Hours",
        "Labor Dollars",
        "Labor Percent",
    ]

    y = height - 76
    c.setFont("Helvetica-Bold", 8)
    for x, h in zip(cols_x, headers):
        if x == 30:
            c.drawString(x, y, h)
        else:
            c.drawRightString(x, y, h)

    rows = [
        ("9:00 AM", "$0.00", "0", "$0.00", "0", "$0.00", "1.68", "$24.90", "100.00%"),
        ("10:00 AM", "$131.27", "9", "$14.59", "9", "$14.59", "7.25", "$102.72", "78.25%"),
        ("11:00 AM", "$847.71", "63", "$13.46", "63", "$13.46", "11.35", "$139.95", "16.51%"),
        ("12:00 PM", "$1,572.09", "128", "$12.28", "128", "$12.28", "19.08", "$232.30", "14.78%"),
        ("1:00 PM", "$962.86", "81", "$11.89", "81", "$11.89", "18.05", "$222.93", "23.15%"),
        ("2:00 PM", "$240.19", "20", "$12.01", "20", "$12.01", "3.80", "$47.86", "19.93%"),
    ]

    c.setFont("Helvetica", 8)
    y -= 14
    for row in rows:
        for x, val in zip(cols_x, row):
            if x == 30:
                c.drawString(x, y, val)
            else:
                c.drawRightString(x, y, val)
        y -= 13

    c.setFont("Helvetica-Bold", 8.5)
    total_row = ("Total", "$3,754.12", "301", "$12.47", "301", "$12.47", "61.22", "$770.67", "20.53%")
    for x, val in zip(cols_x, total_row):
        if x == 30:
            c.drawString(x, y, val)
        else:
            c.drawRightString(x, y, val)

    c.setFont("Helvetica", 8)
    c.drawRightString(width - 30, 25, "Page 1 of 1")
    c.showPage()
    c.save()
    return buf.getvalue()


def build_kitchen_speed_pdf() -> bytes:
    """Generate a realistic borderless 'Kitchen Speed of Service by Hour' PDF matching Image 3."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(25, height - 35, "Kitchen Speed of Service by Hour")
    c.drawCentredString(width * 0.52, height - 35, "10912 - Ellijay, GA - GC-B")

    c.setFont("Helvetica", 8.5)
    c.drawString(25, height - 50, "11/24/2026 1:34:43 PM (UTC-05:00) Eastern Time (US & Canada)")
    c.drawCentredString(width * 0.52, height - 50, "Tuesday, November 24, 2026")

    cols_x = [105, 145, 185, 225, 265, 310, 365, 415, 460, 505, 560, 605, 650, 695, 740, 780]
    headers_line1 = [
        "Hour",
        "DT Chill",
        "DT Hot",
        "DT Expo",
        "FC Hot",
        "FC Chill",
        "FC/DT Fryer",
        "FC/DT OJ",
        "Spare",
        "FC Expo",
        "Menu Item",
        "Spare 1",
        "Spare 2",
        "Spare 3",
        "Spare 4",
        "Average",
    ]
    headers_line2 = {
        8: "Queue",
        10: "Header",
    }

    y = height - 80
    c.setFont("Helvetica-Bold", 7)
    for idx, (x, h) in enumerate(zip(cols_x, headers_line1)):
        c.drawRightString(x, y, h)
        if idx in headers_line2:
            c.drawRightString(x, y - 9, headers_line2[idx])

    rows = [
        ("10:00 AM - 10:59 AM", "1:02:25", "0:00", "12:41", "37:39", "0:00", "43:23", "0:00", "0:00", "14:07", "0:00", "0:00", "0:00", "0:00", "0:00", "34:03"),
        ("11:00 AM - 11:59 AM", "27:20", "6:03", "4:44", "10:07", "33:53", "9:14", "0:00", "0:00", "5:47", "0:00", "0:00", "0:00", "0:00", "0:00", "10:00"),
        ("12:00 PM - 12:59 PM", "3:30", "5:07", "5:00", "7:29", "9:33", "3:30", "0:00", "0:00", "8:37", "0:00", "0:00", "0:00", "0:00", "0:00", "5:36"),
        ("1:00 PM - 1:59 PM", "3:23", "4:43", "8:21", "11:07", "1:57", "2:57", "0:00", "0:00", "5:39", "0:00", "0:00", "0:00", "0:00", "0:00", "5:13"),
    ]

    c.setFont("Helvetica", 7.5)
    y -= 22
    for row in rows:
        for x, val in zip(cols_x, row):
            c.drawRightString(x, y, val)
        y -= 13

    c.setFont("Helvetica-Bold", 7.5)
    avg_row = ("Average", "14:13", "5:15", "5:39", "9:51", "14:48", "5:41", "0:00", "0:00", "7:34", "0:00", "0:00", "0:00", "0:00", "0:00", "")
    for x, val in zip(cols_x, avg_row):
        if val:
            c.drawRightString(x, y, val)

    c.showPage()
    c.save()
    return buf.getvalue()
