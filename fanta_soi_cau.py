import os
import json
import time
import random
import base64
import httpx
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
from openai import OpenAI

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ── AI client ──
# Thứ tự ưu tiên:
#   1. Replit AI Integrations (khi chạy trên Replit)
#   2. GEMINI_API_KEY         (Google Gemini — miễn phí, dùng trên Railway)
#   3. OPENAI_API_KEY         (OpenAI — dự phòng)
_replit_base = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
_replit_key  = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
_gemini_key  = os.environ.get("GEMINI_API_KEY")
_openai_key  = os.environ.get("OPENAI_API_KEY")

if _replit_key:
    # Đang chạy trên Replit → dùng proxy của Replit, model GPT
    _ai_base  = _replit_base
    _ai_key   = _replit_key
    AI_MODEL  = "gpt-5.4"
elif _gemini_key:
    # Chạy trên Railway hoặc máy khác → dùng Gemini miễn phí
    _ai_base  = "https://generativelanguage.googleapis.com/v1beta/openai/"
    _ai_key   = _gemini_key
    AI_MODEL  = "gemini-2.0-flash"
elif _openai_key:
    # Dự phòng: OpenAI thông thường
    _ai_base  = None
    _ai_key   = _openai_key
    AI_MODEL  = "gpt-4o-mini"
else:
    _ai_base  = None
    _ai_key   = None
    AI_MODEL  = ""

openai_client = OpenAI(base_url=_ai_base, api_key=_ai_key or "no-key")
AI_ENABLED    = bool(_ai_key)

# ── File lưu trữ dữ liệu ──
DATA_FILE = "bot_data.json"

def load_data() -> dict:
    """Đọc dữ liệu đã lưu từ file JSON."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"history_fantan": [], "chat_history": {}}

def save_data():
    """Lưu dữ liệu hiện tại vào file JSON (ghi an toàn, không mất dữ liệu)."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "history_fantan": history_fantan,
                "chat_history": {
                    str(k): v for k, v in chat_history.items()
                },
            },
            f, ensure_ascii=False, indent=2,
        )
    os.replace(tmp, DATA_FILE)  # atomic: không bao giờ bị ghi nửa chừng

# Tải dữ liệu khi khởi động
_data = load_data()
history_fantan: list         = _data["history_fantan"]
chat_history: dict[int, list] = {int(k): v for k, v in _data["chat_history"].items()}


# ══════════════════════════════════════════════
#  CÁC HÀM PHÂN TÍCH CẦU FAN-TAN
# ══════════════════════════════════════════════

def kiem_tra_cau_bet(lich_su, so_van=4):
    """Cầu Bệt: cùng 1 số lặp ≥4 ván liên tiếp. VD: 3-3-3-3"""
    if len(lich_su) < so_van:
        return None
    recent = lich_su[-so_van:]
    if all(v == recent[0] for v in recent):
        return (
            f"⚠️ CẦU BỆT [số {recent[0]}]\n"
            f"   → {so_van} ván liên tiếp ra số {recent[0]}\n"
            f"   → Gợi ý: có thể đổi số hoặc tiếp tục theo"
        )
    return None


def kiem_tra_cau_dao_1_1(lich_su, so_van=4):
    """Cầu Đảo 1-1: 2 số xen kẽ đều đặn. VD: 1-3-1-3"""
    if len(lich_su) < so_van:
        return None
    recent = lich_su[-so_van:]
    xen_ke = all(recent[i] != recent[i+1] for i in range(len(recent)-1))
    # Thêm kiểm tra: số 1 và số 3 xen kẽ (không phải 3 số khác nhau)
    nhom = set(recent)
    if xen_ke and len(nhom) == 2:
        ke_tiep = recent[-2]
        return (
            f"🔄 CẦU ĐẢO 1-1\n"
            f"   → Chuỗi: {' - '.join(recent)}\n"
            f"   → Gợi ý: ván tới có thể ra [{ke_tiep}]"
        )
    return None


def kiem_tra_cau_kep_2_2(lich_su, so_van=8):
    """Cầu Kép 2-2: đi theo cặp đôi. VD: 2-2-4-4-2-2-4-4"""
    if len(lich_su) < so_van:
        return None
    recent = lich_su[-so_van:]
    kep = all(recent[i] == recent[i+1] for i in range(0, len(recent)-1, 2))
    chuyen = all(recent[i] != recent[i+2] for i in range(0, len(recent)-2, 2))
    if kep and chuyen:
        return (
            f"🃏 CẦU KÉP 2-2\n"
            f"   → Chuỗi: {' - '.join(recent)}\n"
            f"   → Gợi ý: đặt theo cặp với ván hiện tại"
        )
    return None


def kiem_tra_cau_3_3(lich_su, so_van=6):
    """Cầu Kép 3-3: đi theo bộ ba. VD: 1-1-1-3-3-3"""
    if len(lich_su) < so_van:
        return None
    recent = lich_su[-so_van:]
    nhom_1 = recent[:3]
    nhom_2 = recent[3:]
    if (all(v == nhom_1[0] for v in nhom_1) and
            all(v == nhom_2[0] for v in nhom_2) and
            nhom_1[0] != nhom_2[0]):
        return (
            f"🔢 CẦU KÉP 3-3\n"
            f"   → Chuỗi: {' - '.join(recent)}\n"
            f"   → Gợi ý: chú ý điểm chuyển số sau 3 ván"
        )
    return None


def kiem_tra_cau_nhip_1_2(lich_su, so_van=6):
    """Cầu Nhịp 1-2: 1 ván A rồi 2 ván B. VD: 1-2-2-1-2-2"""
    if len(lich_su) < so_van:
        return None
    r = lich_su[-so_van:]
    nhip = (r[0] != r[1] and r[1] == r[2] and
            r[2] != r[3] and r[3] != r[4] and
            r[4] == r[5] and r[0] == r[3])
    if nhip:
        return (
            f"🔁 CẦU NHỊP 1-2\n"
            f"   → Chuỗi: {' - '.join(r)}\n"
            f"   → Gợi ý: nhịp 1 ván đổi - 2 ván giữ"
        )
    return None


def kiem_tra_cau_lap_so_du(lich_su, so_van=4):
    """Cầu Lặp Số Dư: 2 số xoay vòng theo chu kỳ. VD: 1-3-1-3"""
    if len(lich_su) < so_van:
        return None
    r = lich_su[-so_van:]
    if r[0] == r[2] and r[1] == r[3] and r[0] != r[1]:
        return (
            f"🔂 CẦU LẶP SỐ DƯ\n"
            f"   → Chuỗi: {' - '.join(r)}\n"
            f"   → Gợi ý: đặt theo chu kỳ {r[0]} - {r[1]}"
        )
    return None


def phan_tich_cau(lich_su):
    """Chạy toàn bộ phân tích, trả về chuỗi kết quả."""
    ket_qua = [
        kiem_tra_cau_bet(lich_su),
        kiem_tra_cau_dao_1_1(lich_su),
        kiem_tra_cau_kep_2_2(lich_su),
        kiem_tra_cau_3_3(lich_su),
        kiem_tra_cau_nhip_1_2(lich_su),
        kiem_tra_cau_lap_so_du(lich_su),
    ]
    phat_hien = [k for k in ket_qua if k]
    return "\n".join(phat_hien) if phat_hien else "Chưa phát hiện cầu đặc biệt (cần thêm ván)"


def du_doan_tiep(lich_su):
    """Gợi ý số tiếp theo dựa trên cầu phát hiện được."""
    if len(lich_su) < 2:
        return random.choice(["1", "2", "3", "4"])
    # Nếu cầu bệt → đổi số
    if kiem_tra_cau_bet(lich_su):
        so_khac = [s for s in ["1","2","3","4"] if s != lich_su[-1]]
        return random.choice(so_khac)
    # Nếu cầu đảo → lấy số 2 ván trước
    if kiem_tra_cau_dao_1_1(lich_su) or kiem_tra_cau_lap_so_du(lich_su):
        return lich_su[-2]
    # Mặc định xoay vòng
    return str((int(lich_su[-1]) % 4) + 1)


# ══════════════════════════════════════════════
#  LỆNH /nhap — NHẬP KẾT QUẢ THẬT
# ══════════════════════════════════════════════
async def nhap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global history_fantan

    if not context.args or context.args[0] not in {"1", "2", "3", "4"}:
        await update.message.reply_text(
            "❓ Cách dùng: /nhap <số>\n"
            "Ví dụ: /nhap 1  /nhap 2  /nhap 3  /nhap 4\n\n"
            "Nhập kết quả thật từ bàn Fan-Tan để bot phân tích cầu!"
        )
        return

    kq = context.args[0]
    history_fantan.append(kq)
    if len(history_fantan) > 10:
        history_fantan.pop(0)
    save_data()

    chuoi     = " - ".join(history_fantan)
    phan_tich = phan_tich_cau(history_fantan)
    predict   = du_doan_tiep(history_fantan)

    await update.message.reply_text(
f"""
✅ Đã ghi nhận: Số {kq}

📈 Chuỗi thật ({len(history_fantan)} ván):
{chuoi}

🔍 Phân tích cầu:
{phan_tich}

🔮 Gợi ý ván tới: Số {predict}
🍀 Chỉ mang tính tham khảo!
"""
    )


# ══════════════════════════════════════════════
#  LỆNH /soi — SOI CẦU NGẪU NHIÊN
# ══════════════════════════════════════════════
async def soi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global history_fantan

    kq  = random.choice(["1", "2", "3", "4"])
    num = random.randint(0, 9999)

    history_fantan.append(kq)
    if len(history_fantan) > 10:
        history_fantan.pop(0)
    save_data()

    chuoi     = " - ".join(history_fantan)
    phan_tich = phan_tich_cau(history_fantan)
    predict   = du_doan_tiep(history_fantan)

    await update.message.reply_text(
f"""
🎯 FAN-TAN SOI CẦU

🔢 Số bàn: {num}
📊 Kết quả: {kq}

📈 Chuỗi gần nhất:
{chuoi}

🔍 Phân tích cầu:
{phan_tich}

🔮 Gợi ý ván tới: Số {predict}
🍀 Chỉ mang tính giải trí!
"""
    )


# ══════════════════════════════════════════════
#  LỆNH /thongke — THỐNG KÊ TẦN SUẤT
# ══════════════════════════════════════════════
async def thongke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global history_fantan

    if len(history_fantan) == 0:
        await update.message.reply_text(
            "📊 Chưa có dữ liệu.\n"
            "Dùng /nhap <số> để nhập kết quả trước nhé!"
        )
        return

    tong = len(history_fantan)
    dem = {"1": 0, "2": 0, "3": 0, "4": 0}
    for so in history_fantan:
        if so in dem:
            dem[so] += 1

    # Sắp xếp từ nhiều → ít
    xep_hang = sorted(dem.items(), key=lambda x: x[1], reverse=True)

    # Thanh biểu đồ đơn giản
    def thanh(so_lan, tong_van, do_dai=10):
        filled = round(so_lan / tong_van * do_dai) if tong_van > 0 else 0
        return "█" * filled + "░" * (do_dai - filled)

    dong = []
    for so, so_lan in xep_hang:
        phan_tram = so_lan / tong * 100
        dong.append(
            f"  Số {so}: {thanh(so_lan, tong)} {so_lan}/{tong} ({phan_tram:.1f}%)"
        )

    so_nhieu_nhat = xep_hang[0][0]
    so_it_nhat    = xep_hang[-1][0]

    # Chuỗi gần nhất (5 ván)
    gan_nhat = " - ".join(history_fantan[-5:])

    await update.message.reply_text(
f"""
📊 THỐNG KÊ FAN-TAN ({tong} ván)

{chr(10).join(dong)}

🏆 Ra nhiều nhất : Số {so_nhieu_nhat} ({dem[so_nhieu_nhat]} lần)
📉 Ra ít nhất   : Số {so_it_nhat} ({dem[so_it_nhat]} lần)

🕐 5 ván gần nhất: {gan_nhat}
"""
    )


# ══════════════════════════════════════════════
#  LỆNH /reset — XÓA LỊCH SỬ
# ══════════════════════════════════════════════
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global history_fantan
    history_fantan.clear()
    save_data()
    await update.message.reply_text("🔄 Đã xóa toàn bộ lịch sử. Bắt đầu phiên mới!")


# ══════════════════════════════════════════════
#  LỆNH /caugi — GIẢI THÍCH CÁC LOẠI CẦU
# ══════════════════════════════════════════════
async def caugi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
"""
📖 GIẢI THÍCH CÁC LOẠI CẦU FAN-TAN

⚠️ CẦU BỆT
  Cùng 1 số lặp ≥4 ván liên tiếp.
  VD: 3 - 3 - 3 - 3
  → Gợi ý đổi số hoặc tiếp tục theo.

🔄 CẦU ĐẢO 1-1
  2 số xen kẽ đều đặn.
  VD: 1 - 3 - 1 - 3
  → Đặt số xen kẽ với ván trước.

🃏 CẦU KÉP 2-2
  Đi theo từng cặp đôi.
  VD: 2 - 2 - 4 - 4 - 2 - 2
  → Đặt theo ván hiện tại nếu chưa đủ đôi.

🔢 CẦU KÉP 3-3
  Đi theo từng bộ ba.
  VD: 1 - 1 - 1 - 3 - 3 - 3
  → Chú ý điểm chuyển số sau 3 ván.

🔁 CẦU NHỊP 1-2
  1 ván đổi rồi 2 ván giữ nguyên.
  VD: 1 - 2 - 2 - 1 - 2 - 2
  → Theo nhịp: 1 đổi - 2 giữ.

🔂 CẦU LẶP SỐ DƯ
  2 số xoay vòng theo chu kỳ.
  VD: 1 - 3 - 1 - 3 - 1 - 3
  → Đặt theo chu kỳ 2 số.

💡 Dùng /nhap <số> để nhập kết quả thật,
   bot sẽ tự động phát hiện cầu cho bạn!
"""
    )


# ══════════════════════════════════════════════
#  LỆNH /start
# ══════════════════════════════════════════════
HELP_TEXT = """
🎯 *BOT SOI CẦU FAN-TAN* 🎯

━━━━━━━━━━━━━━━━━━━━
📥 *NHẬP KẾT QUẢ THẬT*
━━━━━━━━━━━━━━━━━━━━
/nhap 1 — Nhập kết quả số 1
/nhap 2 — Nhập kết quả số 2
/nhap 3 — Nhập kết quả số 3
/nhap 4 — Nhập kết quả số 4

💡 _Nhập nhiều ván liên tiếp để bot phân tích cầu chính xác hơn_

━━━━━━━━━━━━━━━━━━━━
🔮 *SOI CẦU & PHÂN TÍCH*
━━━━━━━━━━━━━━━━━━━━
/soi — Soi cầu ngẫu nhiên + phân tích chuỗi hiện tại
/thongke — Thống kê tần suất xuất hiện của 1, 2, 3, 4
/caugi — Giải thích chi tiết các loại cầu Fan-Tan

━━━━━━━━━━━━━━━━━━━━
🤖 *AI THÔNG MINH*
━━━━━━━━━━━━━━━━━━━━
💬 Nhắn tin bất kỳ → AI trả lời tức thì
🖼️ Gửi ảnh (kèm câu hỏi nếu muốn) → AI phân tích ảnh
/xoaai — Xóa lịch sử hội thoại, bắt đầu cuộc trò chuyện mới

━━━━━━━━━━━━━━━━━━━━
⚙️ *QUẢN LÝ*
━━━━━━━━━━━━━━━━━━━━
/reset — Xóa toàn bộ lịch sử cầu, bắt đầu lại từ đầu
/help — Hiển thị hướng dẫn này
/start — Khởi động lại bot

━━━━━━━━━━━━━━━━━━━━
🍀 _Kết quả chỉ mang tính tham khảo, chúc may mắn!_
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Chào mừng bạn đến với *Bot Soi Cầu Fan-Tan*!\n\n"
        "Dùng /help để xem đầy đủ hướng dẫn sử dụng.\n\n"
        "Bắt đầu bằng cách nhập kết quả thật:\n"
        "/nhap 1   /nhap 2   /nhap 3   /nhap 4",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


# ══════════════════════════════════════════════
#  AI CHAT — TIN NHẮN VĂN BẢN
# ══════════════════════════════════════════════
SYSTEM_PROMPT = (
    "Bạn là trợ lý AI thông minh tích hợp trong bot Telegram Fan-Tan soi cầu. "
    "Trả lời bằng tiếng Việt, ngắn gọn, thân thiện. "
    "Nếu người dùng hỏi về Fan-Tan hoặc soi cầu, hãy giải thích rõ ràng."
)

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản thường → gửi cho AI trả lời."""
    if not AI_ENABLED:
        await update.message.reply_text("⚠️ Chức năng AI chưa được cấu hình. Vui lòng thêm OPENAI_API_KEY vào biến môi trường.")
        return

    user_id = update.effective_user.id
    text     = update.message.text

    if user_id not in chat_history:
        chat_history[user_id] = []

    chat_history[user_id].append({"role": "user", "content": text})

    # Giữ tối đa 10 tin nhắn gần nhất để tránh quá dài
    if len(chat_history[user_id]) > 10:
        chat_history[user_id] = chat_history[user_id][-10:]

    await update.message.chat.send_action("typing")

    try:
        response = openai_client.chat.completions.create(
            model=AI_MODEL,
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *chat_history[user_id],
            ],
        )
        reply = response.choices[0].message.content
        chat_history[user_id].append({"role": "assistant", "content": reply})
        save_data()
        await update.message.reply_text(f"🤖 {reply}")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi AI: {str(e)}")


# ══════════════════════════════════════════════
#  AI CHAT — PHÂN TÍCH HÌNH ẢNH
# ══════════════════════════════════════════════
async def ai_anh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh từ người dùng → AI phân tích và mô tả."""
    if not AI_ENABLED:
        await update.message.reply_text("⚠️ Chức năng AI chưa được cấu hình. Vui lòng thêm OPENAI_API_KEY vào biến môi trường.")
        return

    await update.message.chat.send_action("typing")

    try:
        # Lấy ảnh độ phân giải cao nhất
        photo   = update.message.photo[-1]
        file    = await context.bot.get_file(photo.file_id)
        caption = update.message.caption or "Hãy phân tích và mô tả hình ảnh này chi tiết bằng tiếng Việt."

        # Tải ảnh về dưới dạng bytes
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            img_bytes = resp.content

        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        response = openai_client.chat.completions.create(
            model=AI_MODEL,
            max_completion_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                        {"type": "text", "text": caption},
                    ],
                },
            ],
        )
        reply = response.choices[0].message.content
        await update.message.reply_text(f"🖼️ {reply}")

    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi phân tích ảnh: {str(e)}")


# ══════════════════════════════════════════════
#  LỆNH /xoaai — XÓA LỊCH SỬ HỘI THOẠI AI
# ══════════════════════════════════════════════
async def xoa_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_history.pop(user_id, None)
    save_data()
    await update.message.reply_text("🧹 Đã xóa lịch sử hội thoại AI. Bắt đầu cuộc trò chuyện mới!")


# ══════════════════════════════════════════════
#  KHỞI CHẠY
# ══════════════════════════════════════════════
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("nhap",    nhap))
    app.add_handler(CommandHandler("soi",     soi))
    app.add_handler(CommandHandler("thongke", thongke))
    app.add_handler(CommandHandler("reset",   reset))
    app.add_handler(CommandHandler("caugi",   caugi))
    app.add_handler(CommandHandler("xoaai",   xoa_ai))
    app.add_handler(MessageHandler(filters.PHOTO, ai_anh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    return app

if not TOKEN:
    print("LỖI: Không tìm thấy TELEGRAM_BOT_TOKEN trong biến môi trường!")
else:
    retry_delay = 5   # giây chờ trước khi thử lại
    attempt    = 0
    while True:
        attempt += 1
        try:
            print(f"Bot Fan-Tan + AI đang khởi động (lần {attempt})...")
            app = build_app()
            app.run_polling(drop_pending_updates=True)
            # run_polling() kết thúc bình thường → thoát hẳn
            print("Bot dừng bình thường.")
            break
        except KeyboardInterrupt:
            print("Bot dừng do người dùng.")
            break
        except Exception as e:
            print(f"[LỖI] Bot crash: {e}. Thử lại sau {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # tăng dần, tối đa 60s
