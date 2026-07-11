path = r"C:\Users\phiwa\Desktop\CircuitBot\pcb_design\board_model.py"
with open(path, "r") as f:
    content = f.read()

old = '"outline_segments": self.outline_segments,\n        }\n\n    @staticmethod'
new = '"outline_segments": self.outline_segments,\n            "layer_count": self.layer_count,\n        }\n\n    @staticmethod'

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("Patched to_dict successfully")
else:
    print("Target not found, checking...")
    idx = content.find("outline_segments")
    print(repr(content[idx:idx+200]))
