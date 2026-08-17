import sys, io
sys.path.insert(0, "/app")
import fitz

doc = fitz.open()
page = doc.new_page()
x0, y0, x1, y1 = 50, 50, 320, 140
cw = (x1 - x0) / 3
ch = (y1 - y0) / 2
for r in range(3):
    yy = y0 + r * ch
    page.draw_line(fitz.Point(x0, yy), fitz.Point(x1, yy))
for c in range(4):
    xx = x0 + c * cw
    page.draw_line(fitz.Point(xx, y0), fitz.Point(xx, y1))
data = [["Material", "Thickness", "Power"], ["Steel", "1 mm", "1000W"]]
for r in range(2):
    for c in range(3):
        page.insert_text(fitz.Point(x0 + c * cw + 4, y0 + r * ch + 12), data[r][c])
buf = io.BytesIO()
doc.save(buf, garbage=4, deflate=True)
content = buf.getvalue()
doc.close()

doc2 = fitz.open(stream=content, filetype="pdf")
t = doc2[0].find_tables().tables[0]
print("CELLS_TYPE", type(t.cells).__name__)
print("CELLS_LEN", len(t.cells))
print("CELLS_0_TYPE", type(t.cells[0]).__name__)
try:
    print("CELLS_0_0_TYPE", type(t.cells[0][0]).__name__)
    print("CELLS_0_0", t.cells[0][0])
except Exception as e:
    print("CELLS_0_0_ERR", repr(e))
print("HAS_RECTS", hasattr(t, "rects"))
if hasattr(t, "rects"):
    print("RECTS_TYPE", type(t.rects).__name__, "LEN", len(t.rects))
    print("RECTS_0_TYPE", type(t.rects[0]).__name__)
doc2.close()
