"""Anime Telegram bot — Python/Render port of the supplied PHP bot.
Secrets/configuration are read from environment variables only.
"""
from __future__ import annotations
import os, logging, asyncio, html
from datetime import datetime
from aiohttp import web
import aiomysql
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('animebot')
TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').replace(';', ',').split(',') if x.strip().isdigit()}
DB_HOST = os.getenv('DB_HOST', '').strip()
DB_PORT = int(os.getenv('DB_PORT', '3306'))
DB_NAME = os.getenv('DB_NAME', '').strip()
DB_USER = os.getenv('DB_USER', '').strip()
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
CURRENCY = os.getenv('CURRENCY', "so'm")
VIP_PRICE = int(os.getenv('VIP_PRICE', '25000'))
GUIDE_TEXT = os.getenv('GUIDE_TEXT', "📚 Qo‘llanma: kerakli bo‘limni tanlang va anime nomi yoki kodini yuboring.")
SPONSOR_TEXT = os.getenv('SPONSOR_TEXT', 'Reklama va hamkorlik uchun admin bilan bog‘laning.')
START_TEXT = os.getenv('START_TEXT', 'Assalomu alaykum, {name}! 🎬 Anime botiga xush kelibsiz.')
WEB_URL = os.getenv('ANIME_WEB_URL', '').strip()
BOT_ENABLED = os.getenv('BOT_ENABLED', 'true').lower() not in ('0','false','off','no')

pool = None

async def db_init():
    global pool
    if not all((DB_HOST, DB_NAME, DB_USER, DB_PASSWORD)):
        log.warning('MySQL env vars incomplete; DB-backed features will be unavailable until configured.')
        return
    pool = await aiomysql.create_pool(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        db=DB_NAME, charset='utf8mb4', autocommit=True, minsize=1, maxsize=8, connect_timeout=10)
    schema = [
      "CREATE TABLE IF NOT EXISTS user_id (id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(250) NOT NULL UNIQUE, status TEXT NOT NULL, refid VARCHAR(32) NOT NULL DEFAULT '', sana VARCHAR(250) NOT NULL DEFAULT '') CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS kabinet (id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(250) NOT NULL UNIQUE, pul VARCHAR(250) NOT NULL DEFAULT '0', pul2 VARCHAR(250) NOT NULL DEFAULT '0', odam VARCHAR(250) NOT NULL DEFAULT '0', ban TEXT NOT NULL DEFAULT 'unban') CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS animelar (id INT AUTO_INCREMENT PRIMARY KEY, nom TEXT NOT NULL, rams TEXT NOT NULL, qismi TEXT NOT NULL, davlat TEXT NOT NULL, tili TEXT NOT NULL, yili TEXT NOT NULL, janri TEXT NOT NULL, qidiruv INT NOT NULL DEFAULT 0, sana TEXT NOT NULL, aniType TEXT, `like` INT DEFAULT 0, deslike INT DEFAULT 0) CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS anime_datas (data_id INT AUTO_INCREMENT PRIMARY KEY, id TEXT NOT NULL, file_id TEXT NOT NULL, qism TEXT NOT NULL, sana TEXT NULL) CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS channels (id INT AUTO_INCREMENT PRIMARY KEY, channelId VARCHAR(32) NOT NULL, channelType VARCHAR(255) NOT NULL DEFAULT 'channel', channelLink VARCHAR(255) NOT NULL) CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS joinRequests (id INT AUTO_INCREMENT PRIMARY KEY, channelId VARCHAR(32) NOT NULL, userId VARCHAR(255) NOT NULL) CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS status (id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(250) NOT NULL, kun VARCHAR(250) NOT NULL DEFAULT '', date TEXT NOT NULL) CHARACTER SET utf8mb4",
      "CREATE TABLE IF NOT EXISTS send (send_id INT AUTO_INCREMENT PRIMARY KEY, time1 TEXT NOT NULL, time2 TEXT NOT NULL, start_id TEXT NOT NULL, stop_id TEXT NOT NULL, admin_id TEXT NOT NULL, message_id TEXT NOT NULL, reply_markup TEXT NOT NULL, step TEXT NOT NULL, time3 TEXT NOT NULL, time4 TEXT NOT NULL, time5 TEXT NOT NULL) CHARACTER SET utf8mb4"
    ]
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for sql in schema: await cur.execute(sql)
    log.info('MySQL connected and required tables checked.')

async def query(sql, args=(), one=False, all_rows=False):
    if pool is None: return None if one else ([] if all_rows else 0)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            if one: return await cur.fetchone()
            if all_rows: return await cur.fetchall()
            return cur.rowcount

def is_admin(uid): return uid in ADMIN_IDS

def main_keyboard(uid):
    rows = [[KeyboardButton('🔎 Anime izlash')], [KeyboardButton('💎 VIP'), KeyboardButton('💰 Hisobim')],
            [KeyboardButton('➕ Pul kiritish'), KeyboardButton('📚 Qo\'llanma')], [KeyboardButton('💵 Reklama va Homiylik')]]
    if is_admin(uid): rows.append([KeyboardButton('🗄 Boshqarish')])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton('📊 Statistika'), KeyboardButton('✉ Xabar Yuborish')],
        [KeyboardButton('🎥 Animelar sozlash'), KeyboardButton('💳 Hamyonlar')],
        [KeyboardButton('🔎 Foydalanuvchini boshqarish')], [KeyboardButton('📢 Kanallar'), KeyboardButton('🤖 Bot holati')],
        [KeyboardButton('◀️ Orqaga')]], resize_keyboard=True)

async def ensure_user(update: Update, refid=''):
    u = update.effective_user
    if not u: return
    uid = str(u.id); today = datetime.now().strftime('%d.%m.%Y')
    if pool:
        old = await query('SELECT user_id FROM user_id WHERE user_id=%s', (uid,), one=True)
        if not old:
            await query('INSERT INTO user_id(user_id,status,refid,sana) VALUES(%s,%s,%s,%s)', (uid,'Oddiy',refid,today))
            await query("INSERT IGNORE INTO kabinet(user_id,pul,pul2,odam,ban) VALUES(%s,'0','0','0','unban')", (uid,))
            if refid.isdigit() and refid != uid:
                await query('UPDATE kabinet SET odam=CAST(odam AS UNSIGNED)+1 WHERE user_id=%s', (refid,))

def user_state(uid): return query('SELECT * FROM user_id WHERE user_id=%s', (str(uid),), one=True)

async def user_banned(uid):
    row = await query('SELECT ban FROM kabinet WHERE user_id=%s', (str(uid),), one=True)
    return bool(row and row.get('ban') == 'ban')

async def check_subscriptions(bot, uid):
    channels = await query('SELECT channelId,channelLink FROM channels', all_rows=True) or []
    missing=[]
    for ch in channels:
        try:
            member=await bot.get_chat_member(ch['channelId'], uid)
            if member.status in ('left','kicked'): missing.append(ch)
        except TelegramError:
            # If bot cannot inspect a channel, require the user to follow its configured link.
            missing.append(ch)
    return missing

async def gate(update, context, uid):
    missing = await check_subscriptions(context.bot, uid)
    if not missing: return True
    buttons=[]
    for ch in missing:
        link=ch.get('channelLink') or ('https://t.me/'+str(ch['channelId']).lstrip('@'))
        buttons.append([InlineKeyboardButton('➕ Kanalga obuna bo‘lish', url=link)])
    buttons.append([InlineKeyboardButton('✅ Tekshirish', callback_data='checksub')])
    text='Botdan foydalanish uchun quyidagi kanallarga obuna bo‘ling:'
    if update.callback_query: await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.effective_message: await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return False

async def send_start(update, context):
    u=update.effective_user; await ensure_user(update, context.user_data.pop('refid_pending',''))
    if await user_banned(u.id): return
    text=START_TEXT.replace('{name}', html.escape(u.first_name or 'do‘st')).replace('{id}',str(u.id))
    await update.effective_message.reply_text(text, reply_markup=main_keyboard(u.id))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref=''
    if context.args and context.args[0].isdigit(): ref=context.args[0]
    context.user_data['refid_pending']=ref
    await send_start(update, context)

async def search_menu(update, context):
    buttons=[[InlineKeyboardButton('🏷 Anime nomi orqali',callback_data='search_name'),InlineKeyboardButton('📌 Kod orqali',callback_data='search_code')],
             [InlineKeyboardButton('⏱ So‘nggi yuklanganlar',callback_data='latest'),InlineKeyboardButton('👁 Eng ko‘p ko‘rilgan',callback_data='popular')],
             [InlineKeyboardButton('💬 Janr orqali',callback_data='genre'),InlineKeyboardButton('📚 Barcha animelar',callback_data='all')]]
    if WEB_URL: buttons.append([InlineKeyboardButton('🌐 Web Animes',url=WEB_URL)])
    await update.effective_message.reply_text('🔎 Qidiruv turini tanlang:',reply_markup=InlineKeyboardMarkup(buttons))

async def render_anime_list(chat_id, context, rows, title='⬇️ Qidiruv natijalari:'):
    if not rows:
        await context.bot.send_message(chat_id,'❌ Anime topilmadi.'); return
    buttons=[[InlineKeyboardButton(f"{i}. {r['nom'][:55]}",callback_data=f"anime:{r['id']}")] for i,r in enumerate(rows,1)]
    await context.bot.send_message(chat_id,title,reply_markup=InlineKeyboardMarkup(buttons))

async def show_anime(chat_id, context, anime_id):
    a=await query('SELECT * FROM animelar WHERE id=%s',(anime_id,),one=True)
    if not a:
        await context.bot.send_message(chat_id,'❌ Ma’lumot topilmadi.'); return
    await query('UPDATE animelar SET qidiruv=COALESCE(qidiruv,0)+1 WHERE id=%s',(anime_id,))
    caption=(f"<b>🎬 Nomi: {html.escape(str(a['nom']))}</b>\n\n🎥 Qismi: {html.escape(str(a.get('qismi','—')))}\n"
      f"🌍 Davlati: {html.escape(str(a.get('davlat','—')))}\n🇺🇿 Tili: {html.escape(str(a.get('tili','—')))}\n"
      f"📆 Yili: {html.escape(str(a.get('yili','—')))}\n🎞 Janri: {html.escape(str(a.get('janri','—')))}\n\n🔍 Qidirishlar: {int(a.get('qidiruv') or 0)+1}")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('📥 Qismlarni ko‘rish / yuklab olish',callback_data=f"episodes:{anime_id}")]])
    if is_admin(chat_id): kb=InlineKeyboardMarkup([[InlineKeyboardButton('📥 Qismlar',callback_data=f'episodes:{anime_id}')],[InlineKeyboardButton('🗑 Anime o‘chirish',callback_data=f'delanime:{anime_id}')]])
    file_id=str(a.get('rams') or '')
    try:
        if file_id.startswith('B') or file_id.startswith('BA'):
            await context.bot.send_video(chat_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif file_id:
            await context.bot.send_photo(chat_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        else: await context.bot.send_message(chat_id,caption,parse_mode=ParseMode.HTML,reply_markup=kb)
    except TelegramError:
        await context.bot.send_message(chat_id,caption,parse_mode=ParseMode.HTML,reply_markup=kb)

async def show_episodes(chat_id, context, anime_id):
    rows=await query('SELECT data_id,qism FROM anime_datas WHERE id=%s ORDER BY CAST(qism AS UNSIGNED), data_id',(str(anime_id),),all_rows=True)
    if not rows:
        await context.bot.send_message(chat_id,'Hozircha bu anime uchun qismlar qo‘shilmagan.'); return
    kb=[[InlineKeyboardButton(f"▶️ {r['qism']}-qism",callback_data=f"episode:{r['data_id']}")] for r in rows]
    await context.bot.send_message(chat_id,'📥 Kerakli qismni tanlang:',reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; data=q.data
    if await user_banned(uid): return
    if data=='checksub':
        if await gate(update,context,uid): await q.message.reply_text('✅ Obuna tasdiqlandi.',reply_markup=main_keyboard(uid))
        return
    if data.startswith('search_'):
        mode=data.split('_',1)[1]; context.user_data['search_mode']=mode
        prompt={'name':'Anime nomini yuboring:','code':'Anime kodini yuboring:','genre':'Janr nomini yuboring:'}.get(mode,'Qidiruv so‘zini yuboring:')
        await q.message.reply_text(prompt); return
    if data in ('latest','popular','all'):
        if data=='latest': sql='SELECT id,nom FROM animelar ORDER BY id DESC LIMIT 15'
        elif data=='popular': sql='SELECT id,nom FROM animelar ORDER BY qidiruv DESC LIMIT 15'
        else: sql='SELECT id,nom FROM animelar ORDER BY id DESC LIMIT 40'
        rows=await query(sql,all_rows=True); await render_anime_list(q.message.chat_id,context,rows); return
    if data.startswith('anime:'):
        if await gate(update,context,uid): await show_anime(q.message.chat_id,context,data.split(':',1)[1])
        return
    if data.startswith('episodes:'):
        await show_episodes(q.message.chat_id,context,data.split(':',1)[1]); return
    if data.startswith('episode:'):
        row=await query('SELECT * FROM anime_datas WHERE data_id=%s',(data.split(':',1)[1],),one=True)
        if row:
            try: await context.bot.send_video(q.message.chat_id,row['file_id'],caption=f"🎬 {row['qism']}-qism")
            except TelegramError: await context.bot.send_document(q.message.chat_id,row['file_id'],caption=f"🎬 {row['qism']}-qism")
        else: await q.message.reply_text('Qism topilmadi.')
        return
    if data.startswith('delanime:') and is_admin(uid):
        aid=data.split(':',1)[1]; await query('DELETE FROM anime_datas WHERE id=%s',(aid,)); await query('DELETE FROM animelar WHERE id=%s',(aid,)); await q.message.reply_text('🗑 Anime va uning qismlari o‘chirildi.'); return
    if data=='vip_buy':
        await q.message.reply_text(f'💎 VIP narxi: {VIP_PRICE} {CURRENCY}. To‘lovdan so‘ng admin bilan bog‘laning.'); return
    if data=='admin_cancel':
        context.user_data.pop('admin_action',None); await q.message.reply_text('Bekor qilindi.',reply_markup=admin_keyboard()); return
    if data=='admin_add_anime' and is_admin(uid):
        context.user_data['admin_action']='add_anime_name'; await q.message.reply_text('Yangi anime nomini yuboring.'); return
    if data=='admin_add_episode' and is_admin(uid):
        context.user_data['admin_action']='add_episode_id'; await q.message.reply_text('Qism qo‘shiladigan anime kodini yuboring:'); return
    if data=='admin_del_anime' and is_admin(uid):
        context.user_data['admin_action']='delete_anime_id'; await q.message.reply_text('O‘chiriladigan anime kodini yuboring:'); return
    if data.startswith('user:') and is_admin(uid):
        target=data.split(':')[1]; context.user_data['target_user']=target
        await q.message.reply_text(f'Foydalanuvchi {target}: amalni tanlang',reply_markup=InlineKeyboardMarkup([
          [InlineKeyboardButton('💎 VIP berish/olish',callback_data=f'vip:{target}')],
          [InlineKeyboardButton('🔔 Ban/Unban',callback_data=f'ban:{target}')],
          [InlineKeyboardButton('➕ Balans qo‘shish',callback_data=f'addmoney:{target}')],
          [InlineKeyboardButton('➖ Balans ayirish',callback_data=f'remmoney:{target}')]])); return
    if data.startswith('ban:') and is_admin(uid):
        target=data.split(':')[1]; row=await query('SELECT ban FROM kabinet WHERE user_id=%s',(target,),one=True)
        new='unban' if row and row.get('ban')=='ban' else 'ban'
        await query('UPDATE kabinet SET ban=%s WHERE user_id=%s',(new,target)); await q.message.reply_text(f'Foydalanuvchi {target}: {new}'); return
    if data.startswith('vip:') and is_admin(uid):
        target=data.split(':')[1]; row=await query('SELECT status FROM user_id WHERE user_id=%s',(target,),one=True)
        new='Oddiy' if row and row.get('status')=='VIP' else 'VIP'
        await query('UPDATE user_id SET status=%s WHERE user_id=%s',(new,target)); await q.message.reply_text(f'{target} statusi: {new}'); return
    if data.startswith(('addmoney:','remmoney:')) and is_admin(uid):
        action,target=data.split(':',1); context.user_data['admin_action']=action; context.user_data['target_user']=target
        await q.message.reply_text('Miqdorni raqam bilan yuboring:'); return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.effective_message; u=update.effective_user; uid=u.id; text=(msg.text or '').strip()
    await ensure_user(update)
    if await user_banned(uid): return
    if not BOT_ENABLED and not is_admin(uid): await msg.reply_text('⛔️ Bot vaqtincha o‘chirilgan.'); return
    if text=='◀️ Orqaga': context.user_data.clear(); await msg.reply_text('Asosiy menyu',reply_markup=main_keyboard(uid)); return
    if text=='🔎 Anime izlash': await search_menu(update,context); return
    if text=='💎 VIP': await msg.reply_text(f'💎 VIP imkoniyatlari. Narxi: {VIP_PRICE} {CURRENCY}',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💎 VIP haqida / xarid',callback_data='vip_buy')]])); return
    if text=='💰 Hisobim':
        row=await query('SELECT * FROM kabinet WHERE user_id=%s',(str(uid),),one=True) or {}
        us=await query('SELECT status FROM user_id WHERE user_id=%s',(str(uid),),one=True) or {}
        bot=await context.bot.get_me()
        await msg.reply_text(f"💰 <b>Hisobingiz</b>\nID: <code>{uid}</code>\nBalans: {row.get('pul','0')} {CURRENCY}\nTakliflar: {row.get('odam','0')}\nStatus: {us.get('status','Oddiy')}\n\nTaklif havolasi: https://t.me/{bot.username}?start={uid}",parse_mode=ParseMode.HTML); return
    if text=='➕ Pul kiritish': await msg.reply_text('💳 Hisobni to‘ldirish bo‘yicha admin bilan bog‘laning.'); return
    if text=="📚 Qo'llanma" or text=='📚 Qo‘llanma': await msg.reply_text(GUIDE_TEXT); return
    if text=='💵 Reklama va Homiylik': await msg.reply_text(SPONSOR_TEXT); return
    if text=='🗄 Boshqarish' and is_admin(uid): await msg.reply_text('🛠 Admin panel',reply_markup=admin_keyboard()); return
    if text=='📊 Statistika' and is_admin(uid):
        users=await query('SELECT COUNT(*) c FROM user_id',one=True); anime=await query('SELECT COUNT(*) c FROM animelar',one=True)
        await msg.reply_text(f"📊 Foydalanuvchilar: {users['c'] if users else 0}\n🎬 Animelar: {anime['c'] if anime else 0}",reply_markup=admin_keyboard()); return
    if text=='🎥 Animelar sozlash' and is_admin(uid):
        await msg.reply_text('Anime boshqaruvi:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('➕ Anime qo‘shish',callback_data='admin_add_anime')],[InlineKeyboardButton('➕ Qism qo‘shish',callback_data='admin_add_episode')],[InlineKeyboardButton('🗑 Anime o‘chirish',callback_data='admin_del_anime')]])); return
    if text=='📢 Kanallar' and is_admin(uid):
        channels=await query('SELECT channelId,channelLink FROM channels ORDER BY id DESC',all_rows=True) or []
        listing='\n'.join(f"{c['channelId']} — {c['channelLink']}" for c in channels) or 'Hozircha kanal qo‘shilmagan.'
        context.user_data['admin_action']='channel_menu'
        await msg.reply_text('📢 Majburiy obuna kanallari\n'+listing+'\n\nYangi kanalni quyidagi ko‘rinishda yuboring: @kanal, https://t.me/kanal\nO‘chirish uchun: delete @kanal'); return
    if text=='🤖 Bot holati' and is_admin(uid):
        await msg.reply_text('Bot holati Environment Variables dagi BOT_ENABLED orqali boshqariladi. true yoki false qilib o‘zgartiring.',reply_markup=admin_keyboard()); return
    if text=='💳 Hamyonlar' and is_admin(uid):
        await msg.reply_text(f'VIP narxi: {VIP_PRICE} {CURRENCY}. Render Environment Variables: VIP_PRICE, CURRENCY.',reply_markup=admin_keyboard()); return
    if text=='✉ Xabar Yuborish' and is_admin(uid): context.user_data['admin_action']='broadcast'; await msg.reply_text('Barcha foydalanuvchilarga yuboriladigan matnni yuboring.'); return
    if text=='🔎 Foydalanuvchini boshqarish' and is_admin(uid): context.user_data['admin_action']='find_user'; await msg.reply_text('Foydalanuvchi ID sini yuboring:'); return
    action=context.user_data.get('admin_action')
    if action and is_admin(uid):
        if text=='Bekor qilish': context.user_data.clear(); await msg.reply_text('Bekor qilindi.',reply_markup=admin_keyboard()); return
        if action=='channel_menu':
            if text.lower().startswith('delete '):
                channel=text[7:].strip(); await query('DELETE FROM channels WHERE channelId=%s',(channel,)); context.user_data.pop('admin_action',None); await msg.reply_text('Kanal o‘chirildi.',reply_markup=admin_keyboard()); return
            parts=[x.strip() for x in text.split(',',1)]
            if len(parts)!=2 or not parts[0] or not parts[1]: await msg.reply_text('Format: @kanal, https://t.me/kanal'); return
            await query('INSERT INTO channels(channelId,channelType,channelLink) VALUES(%s,%s,%s)',(parts[0],'channel',parts[1])); context.user_data.pop('admin_action',None); await msg.reply_text('Kanal qo‘shildi. Bot kanal admini ekanini tekshiring.',reply_markup=admin_keyboard()); return
        if action=='delete_anime_id':
            if not text.isdigit(): await msg.reply_text('Anime kodi raqam bo‘lishi kerak.'); return
            await query('DELETE FROM anime_datas WHERE id=%s',(text,)); await query('DELETE FROM animelar WHERE id=%s',(text,)); context.user_data.pop('admin_action',None); await msg.reply_text('Anime o‘chirildi.',reply_markup=admin_keyboard()); return
        if action=='add_episode_id':
            if not text.isdigit(): await msg.reply_text('Anime kodi raqam bo‘lishi kerak.'); return
            found=await query('SELECT id,nom FROM animelar WHERE id=%s',(text,),one=True)
            if not found: await msg.reply_text('Bu kod bilan anime topilmadi. Qayta yuboring.'); return
            context.user_data['episode_anime_id']=text; context.user_data['admin_action']='add_episode_number'; await msg.reply_text(f"{found['nom']} uchun qism raqamini yuboring:"); return
        if action=='add_episode_number':
            if not text.isdigit(): await msg.reply_text('Qism raqami raqam bo‘lishi kerak.'); return
            context.user_data['episode_number']=text; context.user_data['admin_action']='add_episode_file'; await msg.reply_text('Endi qism videosini yuboring yoki Telegram file_id ni yuboring.'); return
        if action=='add_episode_file':
            file_id=msg.video.file_id if msg.video else (msg.document.file_id if msg.document else text)
            await query('INSERT INTO anime_datas(id,file_id,qism,sana) VALUES(%s,%s,%s,%s)',(context.user_data['episode_anime_id'],file_id,context.user_data['episode_number'],datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            context.user_data.clear(); await msg.reply_text('✅ Qism qo‘shildi.',reply_markup=admin_keyboard()); return
        if action=='find_user':
            row=await query('SELECT u.user_id,u.status,k.pul,k.odam,k.ban FROM user_id u LEFT JOIN kabinet k ON k.user_id=u.user_id WHERE u.user_id=%s',(text,),one=True)
            if not row: await msg.reply_text('Foydalanuvchi topilmadi.'); return
            context.user_data.pop('admin_action',None)
            await msg.reply_text(f"ID: {row['user_id']}\nStatus: {row['status']}\nBalans: {row.get('pul')}\nTakliflar: {row.get('odam')}\nBan: {row.get('ban')}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Boshqarish',callback_data=f"user:{row['user_id']}")]])); return
        if action in ('addmoney:','remmoney:'):
            try: amount=float(text)
            except ValueError: await msg.reply_text('Faqat raqam yuboring.'); return
            target=context.user_data.get('target_user'); col='pul'
            row=await query('SELECT pul FROM kabinet WHERE user_id=%s',(target,),one=True)
            if not row: await msg.reply_text('Foydalanuvchi topilmadi.'); return
            balance=float(row.get('pul') or 0); balance=balance+amount if action=='addmoney:' else balance-amount
            await query('UPDATE kabinet SET pul=%s,pul2=CAST(pul2 AS DECIMAL(12,2))+%s WHERE user_id=%s',(str(balance),amount if action=='addmoney:' else -amount,target))
            context.user_data.clear(); await msg.reply_text(f'Yangi balans: {balance} {CURRENCY}',reply_markup=admin_keyboard()); return
        if action=='broadcast':
            rows=await query('SELECT user_id FROM user_id',all_rows=True) or []; ok=0; fail=0
            for row in rows:
                try: await context.bot.send_message(int(row['user_id']),text); ok+=1
                except TelegramError: fail+=1
                if ok%25==0: await asyncio.sleep(.1)
            context.user_data.pop('admin_action',None); await msg.reply_text(f'Xabar yuborish yakunlandi. Yetkazildi: {ok}; xato: {fail}',reply_markup=admin_keyboard()); return
        if action=='add_anime_name':
            context.user_data['new_anime']={'nom':text}; context.user_data['admin_action']='add_anime_file'; await msg.reply_text('Endi anime postining rasm yoki video file_id sini yuboring (yoki shu botga video/rasm yuboring).'); return
        if action=='add_anime_file':
            file_id=None
            if msg.photo: file_id=msg.photo[-1].file_id
            elif msg.video: file_id=msg.video.file_id
            elif msg.document: file_id=msg.document.file_id
            else: file_id=text
            context.user_data['new_anime']['rams']=file_id; context.user_data['admin_action']='add_anime_meta'; await msg.reply_text('Ma’lumotlarni vergul bilan yuboring: qismi, davlat, tili, yil, janr.'); return
        if action=='add_anime_meta':
            parts=[x.strip() for x in text.split(',')]
            if len(parts)<5: await msg.reply_text('5 ta qiymat kerak: qismi, davlat, tili, yil, janr.'); return
            a=context.user_data['new_anime']
            await query('INSERT INTO animelar(nom,rams,qismi,davlat,tili,yili,janri,qidiruv,sana,aniType) VALUES(%s,%s,%s,%s,%s,%s,%s,0,%s,%s)',(a['nom'],a['rams'],parts[0],parts[1],parts[2],parts[3],parts[4],datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'anime'))
            context.user_data.clear(); await msg.reply_text('✅ Anime bazaga qo‘shildi.',reply_markup=admin_keyboard()); return
    mode=context.user_data.get('search_mode')
    if mode:
        context.user_data.pop('search_mode',None)
        if not await gate(update,context,uid): return
        if mode=='name': rows=await query('SELECT id,nom FROM animelar WHERE nom LIKE %s ORDER BY id DESC LIMIT 20',(f'%{text}%',),all_rows=True)
        elif mode=='code':
            if text.isdigit(): await show_anime(msg.chat_id,context,int(text)); return
            rows=[]
        elif mode=='genre': rows=await query('SELECT id,nom FROM animelar WHERE janri LIKE %s ORDER BY id DESC LIMIT 20',(f'%{text}%',),all_rows=True)
        else: rows=[]
        await render_anime_list(msg.chat_id,context,rows); return
    # Also allow direct numeric anime code and name lookup as a convenience.
    if text.isdigit():
        row=await query('SELECT id FROM animelar WHERE id=%s',(text,),one=True)
        if row and await gate(update,context,uid): await show_anime(msg.chat_id,context,text)
        return
    rows=await query('SELECT id,nom FROM animelar WHERE nom LIKE %s ORDER BY id DESC LIMIT 10',(f'%{text}%',),all_rows=True)
    if rows: await render_anime_list(msg.chat_id,context,rows,'🔎 Mos animelar:')

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin can submit a photo/video while in the add-anime flow.
    await handle_text(update, context)

async def error_handler(update, context):
    log.exception('Unhandled bot exception', exc_info=context.error)

async def health(request): return web.json_response({'ok': True, 'service': 'anime-telegram-bot'})

async def main():
    if not TOKEN:
        raise RuntimeError('BOT_TOKEN environment variable is missing. Set it in Render → Environment.')
    await db_init()
    application=Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler('start',start))
    application.add_handler(CommandHandler('help',lambda update, context: update.effective_message.reply_text('Bo‘limni tanlang yoki anime nomi/kodini yuboring.')))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_text))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL,handle_media))
    application.add_error_handler(error_handler)
    port=int(os.getenv('PORT','10000'))
    webapp=web.Application(); webapp.router.add_get('/',health); webapp.router.add_get('/health',health)
    runner=web.AppRunner(webapp); await runner.setup(); site=web.TCPSite(runner,'0.0.0.0',port); await site.start()
    log.info('Health server listening on port %s',port)
    async with application:
        await application.start()
        await application.updater.start_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
        log.info('Telegram polling started.')
        try:
            await asyncio.Event().wait()
        finally:
            await application.updater.stop(); await application.stop(); await runner.cleanup()
            if pool: pool.close(); await pool.wait_closed()

if __name__=='__main__': asyncio.run(main())
