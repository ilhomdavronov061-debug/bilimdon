# Bilimdon! — jonli multiplayer viktorina

*Muallif: **Ergashev Asadbek***

Jonli viktorina o‘yini. O‘qituvchi viktorinalarni saytning o‘zida yaratadi, darsda ekranga PIN va QR kod chiqaradi,
talabalar telefondan kirib javob beradi. Tez javob — ko‘proq ball (500–1000), ketma-ket to‘g‘ri javob uchun 🔥 bonus.

## Sahifalar
| Manzil | Kim uchun |
|---|---|
| `/host` | O‘qituvchi paneli: viktorinalar ro‘yxati, yaratish, import, o‘chirish, nusxa |
| `/host/edit?id=…` | Savol muharriri |
| `/host/game?quiz=…` | Proyektor ekrani: PIN + QR, savollar, grafik, reyting, shohsupa |
| `/` | Talabalar (telefon). QR kod skanerlansa PIN avtomatik qo‘yiladi |

## Viktorina yaratish
- **＋ Yangi viktorina** → muharrirda savol, SQL kod (ixtiyoriy), mavzu, vaqt, 2–4 javob, ✓ to‘g‘ri javob, izoh.
- **📋 Matndan import** → savollarni matn ko‘rinishida joylash (to‘g‘ri javobga ✅ yoki *):
  ```
  ━━━ ORDER BY ━━━
  1. Bu so‘rov natijasi nima? (30s)
  SELECT ism FROM talabalar ORDER BY ball DESC LIMIT 1;
  A) Aziza
  B) Sardor ✅
  C) Malika
  ```
- Saqlash: 💾 tugmasi yoki **Ctrl+S**.

## Lokal ishga tushirish (VS Code)
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```
Ochiladi: `http://localhost:8000/host`. Savollar `kahoot.db` (SQLite) faylida saqlanadi.

## Railway’ga deploy
1. Papkani GitHub’ga yuklang → Railway → **New Project → Deploy from GitHub repo**.
2. Shu proyektga **+ New → Database → PostgreSQL** qo‘shing.
3. Servisning **Variables** bo‘limiga:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (savollar redeploy’dan keyin ham saqlanib qoladi)
   - `ADMIN_PASSWORD` = o‘zingiz tanlagan parol (talabalar savollarni ko‘rmasligi uchun)
4. **Settings → Networking → Generate Domain**.

> PostgreSQL ulanmasa, savollar SQLite’da saqlanadi va **har redeploy’da o‘chib ketadi** — shuning uchun 2-qadam muhim.

## Muhit o‘zgaruvchilari
| Nom | Vazifasi |
|---|---|
| `ADMIN_PASSWORD` | O‘qituvchi paneli paroli. Qo‘yilmasa panel hammaga ochiq |
| `DATABASE_URL` | PostgreSQL manzili. Qo‘yilmasa SQLite (`kahoot.db`) |

## Boshqa
- O‘qituvchi sahifani yangilasa ham o‘yin yo‘qolmaydi; talaba telefoni o‘chib-yonsa, o‘sha ism bilan qayta ulanadi.
- PIN yoki QR’ni bossangiz, QR kod butun ekranga kattalashadi.
- Lobby’da talaba ismini bosib, uni o‘yindan chiqarish mumkin.
- Faol o‘yinlar server xotirasida turadi — o‘yin paytida redeploy qilmang.
