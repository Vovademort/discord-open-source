import os
import asyncio
import threading
from collections import deque

import discord
from discord.ext import commands
from flask import Flask

# ---------- Web server for Render health check ----------
app = Flask(__name__)

@app.get("/")
def home():
    return "ok"

def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()
# --------------------------------------------------------

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)

YTDL_OPTS = {
    "format": "bestaudio/best",
    "default_search": "ytsearch",
    "noplaylist": False,
    "quiet": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "cachedir": False,
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

import yt_dlp
ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

# Состояние по серверам
class GuildState:
    def __init__(self):
        self.queue = deque()
        self.playing = False
        self.lock = asyncio.Lock()

states: dict[int, GuildState] = {}

def get_state(guild_id: int) -> GuildState:
    if guild_id not in states:
        states[guild_id] = GuildState()
    return states[guild_id]

async def ensure_voice(ctx):
    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.reply("Ты не в голосовом канале.")
        return None
    dest = ctx.author.voice.channel
    if ctx.voice_client is None:
        vc = await dest.connect()
    else:
        vc = ctx.voice_client
        if vc.channel != dest:
            await vc.move_to(dest)
    return vc

async def resolve_entries(query: str):
    """Возвращает список (title, url) для одиночного трека или плейлиста."""
    info = ytdl.extract_info(query, download=False)
    results = []
    if info.get("_type") == "playlist" and info.get("entries"):
        for e in info["entries"]:
            if not e:
                continue
            title = e.get("title") or "Untitled"
            url = (
                e.get("webpage_url")
                or e.get("url")
                or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get("id") else None)
            )
            if url:
                results.append((title, url))
    else:
        title = info.get("title") or query
        url = info.get("webpage_url") or info.get("url") or query
        results.append((title, url))
    return results

async def play_next(ctx):
    state = get_state(ctx.guild.id)
    if state.playing or not state.queue:
        return

    async with state.lock:
        if state.playing or not state.queue:
            return
        state.playing = True

    try:
        title, url = state.queue[0]
        data = ytdl.extract_info(url, download=False)
        stream_url = data["url"]  # прямой аудио-поток

        source = await discord.FFmpegOpusAudio.from_probe(stream_url, **FFMPEG_OPTS)
        vc = ctx.voice_client
        if vc is None:
            await ensure_voice(ctx)
            vc = ctx.voice_client

        def after_play(err):
            # Запускаем следующий трек
            fut = asyncio.run_coroutine_threadsafe(on_finish(ctx, err), bot.loop)
            try:
                fut.result()
            except Exception:
                pass

        vc.play(source, after=after_play)
        await ctx.send(f"🎵 Now playing: {title}")
    except Exception as e:
        await ctx.send(f"❌ Не удалось проиграть трек: {e}")
        # Снимаем флаг и убираем проблемный трек
        get_state(ctx.guild.id).playing = False
        if state.queue:
            state.queue.popleft()
        await play_next(ctx)

async def on_finish(ctx, err):
    state = get_state(ctx.guild.id)
    if err:
        await ctx.send(f"⚠️ Произошла ошибка во время воспроизведения: {err}")
    if state.queue:
        state.queue.popleft()
    state.playing = False
    if state.queue:
        await play_next(ctx)

@bot.command(name="join")
async def join(ctx):
    vc = await ensure_voice(ctx)
    if vc:
        await ctx.reply(f"Подключился к: **{vc.channel}**")

@bot.command(name="leave")
async def leave(ctx):
    state = get_state(ctx.guild.id)
    state.queue.clear()
    state.playing = False
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.reply("Отключился.")

@bot.command(name="play")
async def play(ctx, *, query: str):
    vc = await ensure_voice(ctx)
    if not vc:
        return
    try:
        entries = await resolve_entries(query)
        state = get_state(ctx.guild.id)
        for title, url in entries:
            state.queue.append((title, url))
        if len(entries) == 1:
            await ctx.send(f"➕ Добавил: **{entries[0][0]}**")
        else:
            await ctx.send(f"➕ Добавил плейлист: **{len(entries)}** трек(ов)")
        await play_next(ctx)
    except Exception as e:
        await ctx.send(f"❌ Ошибка при добавлении: {e}")

@bot.command(name="skip")
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.reply("⏭️ Пропустил.")
    else:
        await ctx.reply("Сейчас ничего не играет.")

@bot.command(name="pause")
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.reply("⏸️ Пауза.")
    else:
        await ctx.reply("Нечего ставить на паузу.")

@bot.command(name="resume")
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.reply("▶️ Продолжаю.")
    else:
        await ctx.reply("Нечего продолжать.")

@bot.command(name="stop")
async def stop(ctx):
    state = get_state(ctx.guild.id)
    state.queue.clear()
    state.playing = False
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.reply("⏹️ Остановил и очистил очередь.")

@bot.command(name="queue")
async def queue_cmd(ctx):
    state = get_state(ctx.guild.id)
    if not state.queue:
        await ctx.reply("Очередь пуста.")
        return
    lines = [f"{i+1}. {t}" for i, (t, _) in enumerate(state.queue)]
    await ctx.reply("**Очередь:**\n" + "\n".join(lines[:20]))

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="!play <запрос>"))

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN не задан в переменных окружения")
bot.run(TOKEN)
