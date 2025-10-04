<img width="545" height="536" alt="Image" src="https://github.com/user-attachments/assets/2bad55b9-5d5a-44e9-a106-2dddc4188a74" />


# Dreammy Droid – Discord Music Bot (YouTube)

บอทเปิดเพลงจาก YouTube สำหรับ Discord
รองรับ: ลิงก์เดี่ยว / เพลย์ลิสต์ (lazy-load) / คิว / ข้าม / หยุด / ปรับเสียง / โหมดดีบั๊ก

## คุณสมบัติ

* เล่นเพลงจาก YouTube (ลิงก์หรือคำค้น)
* รองรับ **เพลย์ลิสต์** แบบ **โหลดเพลงแรกทันที** และค่อยๆ โหลดเพลงถัดไปเมื่อถึงคิว (เร็ว ไม่หน่วงตอนสั่ง `!play`)
* มีคิวเพลง, ข้ามเพลง, หยุด, หยุดชั่วคราว/เล่นต่อ, ดูกำลังเล่น, ดูคิว
* ปรับเสียง `!vol 0–100`
* โหมด **debug** (`!debug on/off`) แสดง log ที่ห้องแชทสำหรับไล่ปัญหา
* ป้องกัน “Already playing audio.” ด้วย **single player loop per guild**

---

## สิ่งที่ต้องมี

* **Python 3.10+**
* **FFmpeg** (จำเป็นต่อการเล่นเสียง)

  * Windows: ดาวน์โหลดจาก ffmpeg.org และเพิ่มลง **PATH**
  * macOS: `brew install ffmpeg`
  * Ubuntu/Debian: `sudo apt install ffmpeg`
* โทเคนบอทจาก **Discord Developer Portal**

---

## สร้างบอทใน Discord

1. ไปที่ [https://discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. แท็บ **Bot** → **Add Bot**
3. (แนะนำ) เปิด **MESSAGE CONTENT INTENT** เพื่ออ่านข้อความคำสั่ง prefix ได้สะดวก
4. กด **Reset Token** (ถ้ายังไม่มี) แล้วคัดลอก **Token**
5. เชิญบอทเข้ากิลด์:

   * ไปที่ **OAuth2 → URL Generator**
   * Scopes: `bot` (และถ้าใช้ slash commands ให้ใส่ `applications.commands`)
   * Bot Permissions (แนะนำสำหรับบอทเพลง):
     `View Channel`, `Send Messages`, `Read Message History`, `Connect`, `Speak`, `Use Voice Activity`, `Embed Links`
   * ใช้ลิงก์ที่ได้ไปเชิญบอทเข้ากิลด์

> สร้างลิงก์แบบกำหนดเอง (แทน `YOUR_CLIENT_ID` ด้วย Application ID):
>
> ```
> https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=3148800&scope=bot%20applications.commands
> ```

---

## ตั้งค่าโปรเจกต์

```bash
git clone <your-repo>
cd <your-repo>
python -m venv .venv
# Windows:
# .venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

**ไฟล์ที่ต้องมี**

`requirements.txt`

```txt
discord.py==2.4.0
yt-dlp>=2024.06,<2026
python-dotenv==1.0.1
PyNaCl==1.5.0
```

`.env` (อย่า commit ไฟล์นี้)

```env
DISCORD_TOKEN=ใส่โทเคนของคุณที่นี่
```

`.gitignore`

```gitignore
.env
.venv/
__pycache__/
*.log
```

---

## รันบอท

```bash
python bot.py
```

ใน Discord (เข้าห้องเสียงก่อน) แล้วพิมพ์:

```
!join
!play https://www.youtube.com/watch?v=xxxxxxxx
!queue
```

---

## คำสั่งทั้งหมด

* `!join` – ให้บอทเข้าห้องเสียงที่คุณอยู่
* `!play <ชื่อ/URL/เพลย์ลิสต์>` – เล่นเพลง / ต่อคิว (เพลย์ลิสต์จะโหลดเพลงแรกทันที ที่เหลือค่อยโหลดเมื่อถึงคิว)
* `!skip` – ข้ามเพลง
* `!stop` – หยุดและล้างคิวทั้งหมด
* `!pause` – พักเพลง
* `!resume` – เล่นต่อ
* `!leave` – ออกจากห้องเสียง
* `!np` – ตอนนี้กำลังเล่นเพลงอะไร
* `!queue` – ดูคิวเพลง (แทร็กที่ยังไม่ resolve จะแสดงเป็น `lazy`)
* `!vol <0–100>` – ปรับความดังเสียง (มีผลกับเพลงถัดไปทันที)
* `!debug on|off` – เปิด/ปิด debug log ในห้องแชท

---

## โครงสถาปัตยกรรม (สั้นๆ)

* ใช้ `yt-dlp` ดึง stream URL จาก YouTube
* เลือกใช้ `FFmpegPCMAudio` (PCM) และส่ง **HTTP headers** ให้ FFmpeg โดยตรง → เสถียรกว่า `from_probe`
* มี **single player loop** ต่อกิลด์ → ไม่ซ้อนกัน ไม่เจอ “Already playing audio.”
* เพลย์ลิสต์: ใช้ `extract_flat` เพื่อให้ดึงรายการได้ไว (lazy-load รายการถัดไปเมื่อต้องเล่นจริง)

---

## Troubleshooting (ที่เจอบ่อย)

### 1) ติดตั้งแล้ว `!join` ไม่ติด / Timeout connecting to voice

* Windows Firewall: อนุญาต **python.exe** ทั้ง **Private/Public** (ต้อง Run PowerShell as Administrator)
* ปิด VPN/Proxy ชั่วคราว
* ลองเปลี่ยน **Voice Region** ของห้องเป็น **Japan/Hong Kong/US East** (กดรูปเฟืองห้องเสียง → Region override)

### 2) Error: `PyNaCl library needed in order to use voice`

ติดตั้ง:

```bash
pip install PyNaCl==1.5.0
```

### 3) ffprobe / from_probe พัง, เล่นแล้วเงียบ

โค้ดนี้ **ไม่ใช้** `from_probe` อยู่แล้ว (เลี่ยงปัญหาเฮดเดอร์) — OK

### 4) “Already playing audio.”

เกิดจากมีหลาย player loop เรียก `vc.play()` พร้อมกัน
โค้ดนี้ใช้ **single loop guard** แล้ว — OK

### 5) GitHub push โดนบล็อก (GH013: secrets)

* **อย่า commit** `.env` และ `.venv/`
* ถ้าหลุดขึ้นไปแล้ว:

  1. **Reset** Discord Token ใน Developer Portal
  2. แก้ `.gitignore` ให้มี `.env` และ `.venv/`, แล้ว `git rm -r --cached .env .venv`
  3. ล้างประวัติทั้ง repo เอาสองอย่างนี้ออกจากทุก commit:

     ```bash
     python -m pip install git-filter-repo
     git filter-repo --force --invert-paths --path .env --path .venv
     git push -u origin main --force
     ```

### 6) Windows: FFmpeg เจอแต่ไม่เล่น

* เช็กว่า `ffmpeg -version` รันได้ใน **Terminal เดียวกับที่รันบอท**
* ในห้องเสียง ให้ดูว่า **บอทไม่โดน Server Mute** และคุณไม่ได้ลดเสียงบอทเหลือ 0

---

## Deploy แนะนำ (ถ้าอยากให้บอทออนไลน์ตลอด)

* **Windows Service**: ใช้ Task Scheduler หรือ NSSM
* **Linux**: ใช้ `systemd` หรือ `pm2`
* **Cloud ฟรี/ถูก**: Railway / Render / Deta (อย่าลืมตั้ง SECRET เป็น `DISCORD_TOKEN` และเตรียม FFmpeg)

---

## ใบอนุญาต

โปรเจกต์นี้สร้างเพื่อการศึกษา/ส่วนตัว ใช้ได้อิสระ (เพิ่ม license ตามต้องการ)

---

