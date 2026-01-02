import time

import mido

# OXI E16 の Port 3 を選ぶ（末尾が "3" の入力名）
port_name = next(
    n for n in mido.get_input_names() if "OXI E16" in n and n.rstrip().endswith("3")
)

print("Listening:", port_name)
inport = mido.open_input(port_name)

# --- 14-bit CC (MSB/LSB) 復元用 ---
msb = {}  # key=(ch, cc) -> 0..127  (cc=0..31)
lsb = {}  # key=(ch, cc) -> 0..127  (cc=0..31)

# --- NRPN 復元用 ---
nrpn_param = {}  # key=ch -> (p_msb, p_lsb)
nrpn_value_msb = {}  # key=ch -> v_msb

try:
    while True:
        for msg in inport.iter_pending():
            # まずは生ログ（必要ならコメントアウト）
            print(f"[RAW] {msg}")

            if msg.type != "control_change":
                continue

            ch = msg.channel  # 0..15
            cc = msg.control
            val = msg.value  # 0..127

            # ---- NRPN 判定・復元 ----
            if cc == 99:  # NRPN parameter MSB
                p_msb, p_lsb = nrpn_param.get(ch, (None, None))
                nrpn_param[ch] = (val, p_lsb)
                continue
            if cc == 98:  # NRPN parameter LSB
                p_msb, p_lsb = nrpn_param.get(ch, (None, None))
                nrpn_param[ch] = (p_msb, val)
                continue
            if cc == 6:  # Data Entry MSB
                nrpn_value_msb[ch] = val
                continue
            if cc == 38:  # Data Entry LSB
                if ch in nrpn_value_msb and ch in nrpn_param:
                    v = (nrpn_value_msb[ch] << 7) | val  # 0..16383
                    p_msb, p_lsb = nrpn_param[ch]
                    if p_msb is not None and p_lsb is not None:
                        p = (p_msb << 7) | p_lsb
                        print(f"[NRPN14] ch={ch+1:02d} param={p} value={v}")
                continue

            # ---- 14-bit CC (MSB/LSB) 判定・復元 ----
            # 標準の high-res CC: MSBはCC0..31、LSBはCC32..63（MSB+32）
            if 0 <= cc <= 31:
                msb[(ch, cc)] = val
                # LSBが既に来ていれば復元して表示
                if (ch, cc) in lsb:
                    v = (msb[(ch, cc)] << 7) | lsb[(ch, cc)]
                    print(f"[CC14] ch={ch+1:02d} cc={cc:02d} value={v}")
                continue
            if 32 <= cc <= 63:
                base = cc - 32
                lsb[(ch, base)] = val
                if (ch, base) in msb:
                    v = (msb[(ch, base)] << 7) | lsb[(ch, base)]
                    print(f"[CC14] ch={ch+1:02d} cc={base:02d} value={v}")
                continue

        time.sleep(0.001)

except KeyboardInterrupt:
    pass
finally:
    inport.close()
