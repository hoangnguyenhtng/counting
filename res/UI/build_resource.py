import os

folder = "res/UI/Icon"
output_file = "res/UI/resources.qrc"

files = os.listdir(folder)

with open(output_file, "w", encoding="utf-8") as f:
    f.write("<RCC>\n")
    f.write('    <qresource prefix="/icons">\n')

    for file in files:
        if file.endswith((".png", ".jpg", ".ico", ".svg")):
            f.write(f"        <file>Icon/{file}</file>\n")

    f.write("    </qresource>\n")
    f.write("</RCC>\n")

print("Build xong file resources.qrc")