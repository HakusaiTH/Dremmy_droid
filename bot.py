import os
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Deque, Dict, List
from collections import deque

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("music-bot")

# ---------------- Discord Intents ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# ---------------- Token ----------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("❌ กรุณาใส่ DISCORD_TOKEN ในไฟล์ .env")

# ---------------- Config ----------------
MAX_ENQUEUE_PER_PLAY = 25  # limit items per !play
FFMPEG_BASE_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

# use for single resolution (fast streamable URL)
YDL_SINGLE = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "http_headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    },
}

# use for playlists: flat listing so it's instant
YDL_PLAYLIST_FLAT = {
    "quiet": True,
    "extract_flat": "in_playlist",  # entries will be shallow (no stream URLs)
    "default_search": "ytsearch",
}

# ---------------- Models ----------------
@dataclass
class Track:
    title: str
    webpage_url: str
    requester: str
    url: Optional[str] = None            # stream url (filled when resolved)
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        return self.url is not None

@dataclass
class GuildMusic:
    queue: Deque[Track] = field(default_factory=deque)
    now_playing: Optional[Track] = None
    play_next_event: asyncio.Event = field(default_factory=asyncio.Event)
    volume: float = 0.8
    debug: bool = False
    player_task: Optional[asyncio.Task] = None  # single loop per guild

    def clear(self):
        self.queue.clear()
        self.now_playing = None
        self.play_next_event = asyncio.Event()

# ---------------- Cog ----------------
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.music: dict[int, GuildMusic] = {}

    def gm(self, guild_id: int) -> GuildMusic:
        if guild_id not in self.music:
            self.music[guild_id] = GuildMusic()
        return self.music[guild_id]

    # ---------- Debug helper ----------
    async def _d(self, ctx: commands.Context, msg: str):
        log.debug(msg)
        gm = self.gm(ctx.guild.id)
        if gm.debug:
            await ctx.send(f"`[debug]` {msg}")

    # ---------- FFmpeg headers builder ----------
    def _build_ffmpeg_before(self, headers: Dict[str, str]) -> str:
        if not headers:
            return FFMPEG_BASE_BEFORE
        lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        return f'{FFMPEG_BASE_BEFORE} -headers "{lines}"'

    async def _create_audio_source(self, track: Track):
        """Create PCM source with headers (no ffprobe)."""
        before = self._build_ffmpeg_before(track.headers)
        return discord.FFmpegPCMAudio(
            track.url,  # type: ignore[arg-type]
            before_options=before,
            options="-vn -loglevel error",
        )

    # ---------- Resolution ----------
    async def resolve_single(self, query_or_url: str, requester: str) -> Track:
        """Resolve a single URL/search to a streamable Track."""
        loop = asyncio.get_running_loop()

        def _do():
            with yt_dlp.YoutubeDL(YDL_SINGLE) as ydl:
                info = ydl.extract_info(query_or_url, download=False)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]
                url = info["url"]
                title = info.get("title", "Unknown title")
                page = info.get("webpage_url") or info.get("original_url") or query_or_url
                headers = info.get("http_headers") or ydl.params.get("http_headers") or {}
                return title, page, url, headers

        title, page, stream, headers = await loop.run_in_executor(None, _do)
        return Track(title=title, webpage_url=page, requester=requester, url=stream, headers=headers)

    async def extract_lazy(self, query: str, requester: str) -> List[Track]:
        """
        Return: [first (resolved), rest (deferred)]
        - non-playlist -> [resolved single]
        - playlist     -> first resolved; rest lazy (url=None)
        """
        await asyncio.sleep(0)  # yield
        loop = asyncio.get_running_loop()

        def _flat():
            with yt_dlp.YoutubeDL(YDL_PLAYLIST_FLAT) as ydl:
                return ydl.extract_info(query, download=False)

        info = await loop.run_in_executor(None, _flat)

        # single
        if "entries" not in info or not info["entries"]:
            only = await self.resolve_single(query, requester)
            return [only]

        # playlist (flat entries)
        entries = [e for e in info["entries"] if e]
        if not entries:
            only = await self.resolve_single(query, requester)
            return [only]

        # resolve first now
        first_url = entries[0].get("url") or f"https://www.youtube.com/watch?v={entries[0].get('id')}"
        first_track = await self.resolve_single(first_url, requester)

        # defer the rest
        deferred: List[Track] = []
        for e in entries[1:]:
            page = e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}"
            title = e.get("title", "Unknown title")
            deferred.append(Track(title=title, webpage_url=page, requester=requester))

        return [first_track] + deferred

    # ---------- Player Loop (single instance guard) ----------
    async def player_loop(self, ctx: commands.Context):
        gm = self.gm(ctx.guild.id)

        # guard: ensure single loop per guild
        current = asyncio.current_task()
        if gm.player_task is not None and gm.player_task is not current and not gm.player_task.done():
            return
        gm.player_task = current

        try:
            while True:
                gm.play_next_event.clear()

                if not gm.queue:
                    gm.now_playing = None
                    try:
                        await asyncio.wait_for(gm.play_next_event.wait(), timeout=300.0)
                    except asyncio.TimeoutError:
                        if ctx.voice_client and ctx.voice_client.is_connected():
                            await ctx.send("⏹️ ไม่มีเพลงในคิว บอทจะออกจากห้องแล้วจ้า")
                            await ctx.voice_client.disconnect(force=True)
                        return
                    continue

                # ensure voice connected
                vc: Optional[discord.VoiceClient] = ctx.voice_client
                for _ in range(50):  # ~10s
                    if vc and vc.is_connected():
                        break
                    await asyncio.sleep(0.2)
                    vc = ctx.voice_client
                if not vc or not vc.is_connected():
                    await ctx.send("ยังไม่เชื่อมต่อห้องเสียง ลอง !join ใหม่จ้า")
                    await asyncio.sleep(1)
                    continue

                # pop next track
                track = gm.queue.popleft()
                if not track.is_resolved:
                    await self._d(ctx, f"Resolving stream for: {track.title}")
                    try:
                        resolved = await self.resolve_single(track.webpage_url, track.requester)
                        track.url = resolved.url
                        track.headers = resolved.headers
                        track.title = resolved.title or track.title
                    except Exception as e:
                        await ctx.send(f"⚠️ ดึงลิงก์สตรีมไม่ได้สำหรับ: **{track.title}** ({e}) — ข้าม")
                        gm.play_next_event.set()
                        continue

                gm.now_playing = track

                try:
                    pcm_src = await self._create_audio_source(track)
                    source = discord.PCMVolumeTransformer(pcm_src, volume=gm.volume)
                except Exception as e:
                    await ctx.send(f"ไม่สามารถเตรียมสตรีมได้: {e}")
                    gm.play_next_event.set()
                    continue

                def _after(err):
                    if err:
                        log.error("Player error: %s", err)
                    self.bot.loop.call_soon_threadsafe(gm.play_next_event.set)

                try:
                    vc.play(source, after=_after)
                except discord.ClientException as e:
                    # should not happen with single-loop guard
                    await ctx.send(f"ไม่สามารถเล่นได้ตอนนี้: {e}")
                    gm.play_next_event.set()
                    continue

                await ctx.send(f"▶️ กำลังเล่น: **{track.title}** | ขอโดย **{track.requester}**\n<{track.webpage_url}>")
                await self._d(ctx, f"Now playing @volume={gm.volume:.2f}")

                await gm.play_next_event.wait()

        finally:
            if gm.player_task is asyncio.current_task():
                gm.player_task = None

    # ---------- Connect helper ----------
    async def _connect_with_retry(self, channel: discord.VoiceChannel, tries: int = 3, timeout: float = 45.0):
        last_err = None
        for attempt in range(1, tries + 1):
            try:
                vc = await channel.connect(reconnect=True, timeout=timeout, self_deaf=True)
                for _ in range(75):  # ~15s
                    if vc.is_connected():
                        return vc
                    await asyncio.sleep(0.2)
                last_err = TimeoutError("VoiceClient object created but not connected")
            except Exception as e:
                last_err = e
            await asyncio.sleep(2 * attempt)
        raise last_err

    # ---------- Commands ----------
    @commands.command(name="debug", help="เปิด/ปิด debug ในแชท: !debug on | !debug off")
    async def debug(self, ctx: commands.Context, mode: Optional[str] = None):
        gm = self.gm(ctx.guild.id)
        if mode is None:
            return await ctx.send(f"debug = **{gm.debug}** (ใช้ `!debug on` หรือ `!debug off`)")
        m = mode.lower()
        if m in ("on", "true", "1", "yes"):
            gm.debug = True
        elif m in ("off", "false", "0", "no"):
            gm.debug = False
        else:
            return await ctx.send("ใช้ `!debug on` หรือ `!debug off` เท่านั้น")
        await ctx.send(f"debug = **{gm.debug}**")

    @commands.command(name="join", help="ให้บอทเข้าห้องเสียงที่คุณอยู่")
    async def join(self, ctx: commands.Context):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            return await ctx.send("คุณต้องอยู่ในห้องเสียงก่อนน้าา")
        channel = ctx.author.voice.channel
        vc = ctx.voice_client

        if vc and vc.channel.id == channel.id and vc.is_connected():
            return await ctx.send(f"อยู่ในห้อง **{channel}** อยู่แล้วจ้า")

        if vc is None:
            try:
                await self._connect_with_retry(channel, tries=3, timeout=45.0)
            except Exception as e:
                return await ctx.send(
                    "เชื่อมต่อห้องเสียงไม่สำเร็จ 😥\n"
                    "• อนุญาต python.exe ในไฟร์วอลล์\n"
                    "• ปิด VPN/Proxy\n"
                    "• เปลี่ยน Voice Region\n"
                    f"รายละเอียด: `{e}`"
                )
        else:
            await vc.move_to(channel)
            for _ in range(75):
                if vc.is_connected():
                    break
                await asyncio.sleep(0.2)
            else:
                return await ctx.send("ย้ายห้องไม่สำเร็จ ลองเปลี่ยน region แล้ว !join ใหม่จ้า")

        await ctx.send(f"เข้าห้องเสียง: **{channel}** แล้วจ้า")

    @commands.command(name="play", help="เล่นเพลงจากชื่อ/URL/เพลย์ลิสต์ (โหลดแทร็กแรกทันที ที่เหลือโหลดเมื่อถึงคิว)")
    async def play(self, ctx: commands.Context, *, query: str):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            return await ctx.send("คุณต้องอยู่ในห้องเสียงก่อนน้าา")

        if ctx.voice_client is None:
            try:
                await self._connect_with_retry(ctx.author.voice.channel, tries=3, timeout=45.0)
            except Exception as e:
                return await ctx.send(
                    "เชื่อมต่อห้องเสียงไม่สำเร็จ 😥 (ก่อนเล่น)\n"
                    "• อนุญาต python.exe ในไฟร์วอลล์\n"
                    "• ปิด VPN/Proxy\n"
                    "• เปลี่ยน Voice Region\n"
                    f"รายละเอียด: `{e}`"
                )

        gm = self.gm(ctx.guild.id)

        try:
            tracks = await self.extract_lazy(query, requester=str(ctx.author))
        except Exception as e:
            return await ctx.send(f"ไม่สามารถดึงรายการเพลงได้: {e}")

        if not tracks:
            return await ctx.send("ไม่พบเพลงที่จะเล่น 😥")

        to_add = tracks[:MAX_ENQUEUE_PER_PLAY]
        for t in to_add:
            gm.queue.append(t)

        if len(to_add) == 1:
            await ctx.send(f"➕ เพิ่มเข้าคิว: **{to_add[0].title}**")
        else:
            await ctx.send(f"📥 เพิ่มเข้าคิว: **{len(to_add)}** เพลง (โหลดละเอียดเมื่อถึงคิว)")

        # start or wake the single loop
        if ctx.voice_client and ctx.voice_client.is_connected():
            if gm.player_task is None or gm.player_task.done():
                gm.player_task = ctx.bot.loop.create_task(self.player_loop(ctx))
            gm.play_next_event.set()

    @commands.command(name="skip", help="ข้ามเพลงที่กำลังเล่น")
    async def skip(self, ctx: commands.Context):
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            return await ctx.send("บอทยังไม่ได้อยู่ในห้องเสียง")
        if vc.is_playing():
            vc.stop()
            await ctx.send("⏭️ ข้ามเพลงแล้ว")

    @commands.command(name="stop", help="หยุดและล้างคิวทั้งหมด")
    async def stop(self, ctx: commands.Context):
        gm = self.gm(ctx.guild.id)
        gm.clear()
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.send("⏹️ หยุดและล้างคิวแล้ว")

    @commands.command(name="pause", help="พักเพลงชั่วคราว")
    async def pause(self, ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ พักเพลงแล้ว")

    @commands.command(name="resume", help="เล่นต่อ")
    async def resume(self, ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ เล่นต่อแล้ว")

    @commands.command(name="leave", help="ให้ออกจากห้องเสียง")
    async def leave(self, ctx: commands.Context):
        if ctx.voice_client:
            await ctx.voice_client.disconnect(force=True)
            await ctx.send("👋 ออกจากห้องเสียงแล้ว")
        gm = self.gm(ctx.guild.id)
        gm.clear()

    @commands.command(name="np", help="ตอนนี้กำลังเล่นอะไร")
    async def nowplaying(self, ctx: commands.Context):
        gm = self.gm(ctx.guild.id)
        if gm.now_playing:
            await ctx.send(
                f"🎵 ตอนนี้: **{gm.now_playing.title}** | ขอโดย **{gm.now_playing.requester}**\n<{gm.now_playing.webpage_url}>"
            )
        else:
            await ctx.send("ตอนนี้ยังไม่มีเพลงกำลังเล่น")

    @commands.command(name="queue", help="ดูคิวเพลง (lazy = ยังไม่ resolve)")
    async def queue(self, ctx: commands.Context):
        gm = self.gm(ctx.guild.id)
        if not gm.queue:
            return await ctx.send("คิวว่างอยู่จ้า")
        lines = []
        for i, t in enumerate(list(gm.queue)[:10], start=1):
            flag = "" if t.is_resolved else " (lazy)"
            lines.append(f"**{i}.** {t.title}{flag} — `{t.requester}`")
        more = f"\n...และอีก {len(gm.queue)-10} เพลง" if len(gm.queue) > 10 else ""
        await ctx.send("📜 **คิวเพลง:**\n" + "\n".join(lines) + more)

    @commands.command(name="vol", help="ปรับเสียง 0-100")
    async def volume(self, ctx: commands.Context, vol: int):
        vol = max(0, min(100, vol))
        gm = self.gm(ctx.guild.id)
        gm.volume = vol / 100.0
        await ctx.send(f"🔊 ตั้งเสียงไว้ที่ {vol}% แล้ว")

# ---------------- Boot ----------------
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def setup_hook():
    await bot.add_cog(MusicCog(bot))

@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="!play"
    ))

@bot.command(name="help")
async def _help(ctx: commands.Context):
    cmds = [
        "**!join** — ให้บอทเข้าห้องเสียง",
        "**!play <ชื่อ/URL/เพลย์ลิสต์>** — เพลงแรกโหลดทันที เพลงถัดไปโหลดเมื่อถึงคิว",
        "**!skip** — ข้ามเพลง",
        "**!stop** — หยุดและล้างคิว",
        "**!pause** — พักเพลง",
        "**!resume** — เล่นต่อ",
        "**!leave** — ออกจากห้องเสียง",
        "**!np** — ตอนนี้กำลังเล่นอะไร",
        "**!queue** — ดูคิวเพลง (lazy = ยังไม่ resolve)",
        "**!vol <0-100>** — ปรับความดัง",
        "**!debug on/off** — เปิดปิด debug ในแชท",
    ]
    await ctx.send("**คำสั่งบอทเพลง:**\n" + "\n".join(cmds))

bot.run(TOKEN)
