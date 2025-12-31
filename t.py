import mido

print("IN ports:")
for name in mido.get_input_names():
    print(" -", name)

# 使いたいポート名に置き換える
port_name = mido.get_input_names()[1]
print("\nListening:", port_name)

with mido.open_input(port_name) as inport:
    for msg in inport:
        print(msg)
