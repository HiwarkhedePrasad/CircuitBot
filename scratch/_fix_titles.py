path = r"C:\Users\phiwa\Desktop\CircuitBot\static\index.html"
with open(path, "r") as f:
    c = f.read()
c = c.replace('title="Pan board"', 'title="Pan board (H)"')
c = c.replace('title="Select and move components"', 'title="Select and move components (S)"')
c = c.replace('title="Route traces"', 'title="Route traces (R)"')
c = c.replace('title="Place vias"', 'title="Place vias (V)"')
with open(path, "w") as f:
    f.write(c)
print("Done")
