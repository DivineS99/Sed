import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal, getcontext, ROUND_HALF_UP, InvalidOperation
from random import seed
from string import ascii_lowercase
from time import time
from typing import Dict, Any
from uuid import uuid4

import aiofiles
import aiofiles.os
import matplotlib.pyplot as plt
from aiocache import cached
from aiogram import executor, types
from aiogram.types.message import ContentTypes
from aiogram.utils.exceptions import TelegramAPIError, BadRequest, MigrateToChat
from aiogram.utils.markdown import quote_html
from matplotlib.dates import DateFormatter
from matplotlib.ticker import MaxNLocator

from constants import (
    bot, on9bot, dp, VIP, VIP_GROUP, ADMIN_GROUP_ID, OFFICIAL_GROUP_ID, WORD_ADDITION_CHANNEL_ID,
    GAMES, pool, PROVIDER_TOKEN, GameState, GameSettings, update_words, ADD_TO_GROUP_KEYBOARD
)
from game import (
    ClassicGame, HardModeGame, ChaosGame, ChosenFirstLetterGame, BannedLettersGame,
    RequiredLetterGame, EliminationGame, MixedEliminationGame
)
from utils import send_admin_group, amt_donated, check_word_existence, has_star, filter_words

seed(time())
getcontext().rounding = ROUND_HALF_UP
build_time = datetime.now().replace(microsecond=0)
MAINT_MODE = False


async def private_only_command(message: types.Message) -> None:
    await message.reply("Please use this command in private.")


async def groups_only_command(message: types.Message) -> None:
    await message.reply("This command can only be used in groups.", reply_markup=ADD_TO_GROUP_KEYBOARD)


@dp.message_handler(is_group=False, commands="start")
async def cmd_start(message: types.Message) -> None:
    # Handle deep links
    arg = message.get_args()
    if arg == "help":
        await cmd_help(message)
        return
    if arg == "donate":
        await send_donate_msg(message)
        return

    await message.reply(
        (
            "Hi! I host games of word chain in Telegram groups.\n"
            "Add me to a group to start playing games!"
        ),
        disable_web_page_preview=True,
        reply_markup=ADD_TO_GROUP_KEYBOARD,
    )


@dp.message_handler(content_types=types.ContentTypes.NEW_CHAT_MEMBERS)
async def new_member(message: types.Message) -> None:
    if any(user.id == bot.id for user in message.new_chat_members):  # self added to group
        await message.reply(
            "Thanks for adding me. Start a classic game with /startclassic!",
            reply=False,
        )
    elif message.chat.id == OFFICIAL_GROUP_ID:
        await message.reply(
            "Welcome to the official On9 Word Chain group!\n"
            "Start a classic game with /startclassic!"
        )


@dp.message_handler(commands="help")
async def cmd_help(message: types.Message) -> None:
    if message.chat.id < 0:
        await message.reply(
            "Please use this command in private.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            "Send help message in private",
                            url="https://t.me/on9wordchainbot?start=help",
                        )
                    ]
                ]
            ),
        )
        return

    await message.reply(
        (
            "/gameinfo - Game mode descriptions\n"
            "/troubleshoot - See how to solve common issues\n"
            "/reqaddword - Request addition of words\n\n"
            "You may message [Jono](tg://user?id=463998526) in *English or Cantonese* for anything about the bot.\n"
            "Official Group: @on9wordchain\n"
            "Word Additions Channel (with status updates): @on9wcwa\n"
            "Source Code: [Tr-Jono/on9wordchainbot](https://github.com/Tr-Jono/on9wordchainbot)\n"
            "Epic icon designed by [Adri](tg://user?id=303527690)"
        ),
        disable_web_page_preview=True,
    )


@dp.message_handler(commands="gameinfo")
async def cmd_gameinfo(message: types.Message) -> None:
    if message.chat.id < 0:
        await private_only_command(message)
        return
    await message.reply(
        "/startclassic - Classic game\n"
        "Players take turns to send words starting with the last letter of the previous word.\n\n"
        "Other modes:\n"
        "/starthard - Hard mode game\n"
        "/startchaos - Chaos game (random turn order)\n"
        "/startcfl - Chosen first letter game\n"
        "/startbl - Banned letters game\n"
        "/startrl - Required letter game\n\n"
        "/startelim - Elimination game\n"
        "Each player's score is their cumulative word length. "
        "The lowest scoring players are eliminated after each round.\n\n"
        "/startmelim - Mixed elimination game (donation reward)\n"
        "Elimination game with different modes. Try at @on9wordchain."
    )


@dp.message_handler(commands="troubleshoot")
async def cmd_troubleshoot(message: types.Message) -> None:
    if message.chat.id < 0:
        await private_only_command(message)
        return
    await message.reply(
        "If you cannot start games in your group:\n"
        "1. If I say maintenance mode is on, an update is waiting to be deployed.\\*\n"
        "2. Make sure I am present and not muted in your group, and slow mode is off.\n"
        "3. Send `/ping@on9wordchainbot` in your group.\n\n"
        "If I respond:\n"
        "Contact [my owner](tg://user?id=463998526) with your group's id (obtained with /groupid).\n\n"
        "If I do not respond:\n"
        "a. I may be offline since an update is being deployed.\\*\n"
        "b. If a group member has spammed me with commands, "
        "I am rate limited for minutes or even hours by Telegram. "
        "Do not spam commands and try again later.\n\n"
        "\\*: Please wait and check @on9wcwa for status updates.\n\n"
        "If you cannot add me into your group:\n"
        "1. Your group may have disabled the addition of new members.\n"
        "2. There can be at most 20 bots in a group. Check if the limit is reached.\n"
        "3. Contact your group admin for help. This is not an issue my owner can resolve.\n\n"
        "If you encounter other issues, please contact [my owner](tg://user?id=463998526)."
    )


@dp.message_handler(commands="ping")
async def cmd_ping(message: types.Message) -> None:
    t = time()
    msg = await message.reply("Pong!")
    await msg.edit_text(f"Pong! `{time() - t:.3f}s`")


@dp.message_handler(commands="groupid")
async def cmd_groupid(message: types.Message) -> None:
    if message.chat.id < 0:
        await message.reply(f"`{message.chat.id}`")
    else:
        await message.reply("Run this command inside a group.")


@dp.message_handler(commands="runinfo")
async def cmd_runinfo(message: types.Message) -> None:
    uptime = datetime.now().replace(microsecond=0) - build_time
    await message.reply(
        f"Build time: `{'{0.day}/{0.month}/{0.year}'.format(build_time)} {str(build_time).split()[1]} HKT`\n"
        f"Uptime: `{uptime.days}.{str(uptime).rsplit(maxsplit=1)[-1]}`\n"
        f"Total games: `{len(GAMES)}`\n"
        f"Running games: `{len([g for g in GAMES.values() if g.state == GameState.RUNNING])}`\n"
        f"Players: `{sum(len(g.players) for g in GAMES.values())}`"
    )


@dp.message_handler(is_owner=True, commands="playinggroups")
async def cmd_playinggroups(message: types.Message) -> None:
    if not GAMES:
        await message.reply("No groups are playing games.")
        return
    groups = []

    async def append_group(group_id: int) -> None:
        try:
            group = await bot.get_chat(group_id)
            url = await group.get_url()
            # TODO: resolve weakref exception, possibly aiogram bug?
        except Exception as e:
            text = f"(<code>{e.__class__.__name__}: {e}</code>)"
        else:
            if url:
                text = f"<a href='{url}'>{quote_html(group.title)}</a>"
            else:
                text = f"<b>{group.title}</b>"
        groups.append(
            text + (
                f" <code>{group_id}</code> "
                f"{len(GAMES[group_id].players_in_game)}/{len(GAMES[group_id].players)}P "
                f"Timer: {GAMES[group_id].time_left}s"
            )
        )

    await asyncio.gather(*[append_group(gid) for gid in GAMES])
    await message.reply("\n".join(groups), parse_mode=types.ParseMode.HTML, disable_web_page_preview=True)


@dp.message_handler(commands=["exist", "exists"])
async def cmd_exists(message: types.Message) -> None:
    word = message.text.partition(" ")[2].lower()
    if not word or not all(c in ascii_lowercase for c in word):  # No proper argument given
        rmsg = message.reply_to_message
        if rmsg and rmsg.text and all(c in ascii_lowercase for c in rmsg.text.lower()):
            word = rmsg.text.lower()
        else:
            await message.reply(
                "Function: Check if a word is in my dictionary. "
                "Use /reqaddword if you want to request addition of new words.\n"
                "Usage: `/exists word`"
            )
            return
    if check_word_existence(word):
        await message.reply(f"_{word.capitalize()}_ is *in* my dictionary.")
    else:
        await message.reply(f"_{word.capitalize()}_ is *not in* my dictionary.")


@dp.message_handler(commands=["startclassic", "startgame"])
async def cmd_startclassic(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return
    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:  # Only stop people from starting games, not joining
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return
    game = ClassicGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="starthard")
async def cmd_starthard(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = HardModeGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startchaos")
async def cmd_startchaos(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = ChaosGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startcfl")
async def cmd_startcfl(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = ChosenFirstLetterGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startbl")
async def cmd_startbl(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = BannedLettersGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startrl")
async def cmd_startrl(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = RequiredLetterGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startelim")
async def cmd_startelim(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = EliminationGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="startmelim")
async def cmd_startmixedelim(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    if (
            message.chat.id not in VIP_GROUP
            and message.from_user.id not in VIP
            and (await amt_donated(message.from_user.id)) < 30
    ):
        await message.reply(
            "This game mode is a donation reward.\n"
            "You can try this game mode at @on9wordchain."
        )
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
        return
    if MAINT_MODE:
        await message.reply("Maintenance mode is on. Games are temporarily disabled.")
        return

    game = MixedEliminationGame(message.chat.id)
    GAMES[group_id] = game
    await game.main_loop(message)


@dp.message_handler(commands="join")
async def cmd_join(message: types.Message) -> None:
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].join(message)
    # No reply is given when there is no running game in case the user was joining another game


@dp.message_handler(is_group=True, is_owner=True, commands="forcejoin")
async def cmd_forcejoin(message: types.Message) -> None:
    group_id = message.chat.id
    rmsg = message.reply_to_message
    if group_id not in GAMES:
        return
    if rmsg and rmsg.from_user.is_bot:  # On9Bot only
        if rmsg.from_user.id != on9bot.id:
            return
        if isinstance(GAMES[group_id], EliminationGame):
            await message.reply(
                "Sorry, [On9Bot](https://t.me/On9Bot) can't play elimination games.",
                disable_web_page_preview=True,
            )
            return
    await GAMES[message.chat.id].forcejoin(message)


@dp.message_handler(is_group=True, commands="extend")
async def cmd_extend(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].extend(message)


@dp.message_handler(is_group=True, is_admin=True, commands="forcestart")
async def cmd_forcestart(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES and GAMES[group_id].state == GameState.JOINING:
        GAMES[group_id].time_left = -99999


@dp.message_handler(is_group=True, commands="flee")
async def cmd_flee(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].flee(message)


@dp.message_handler(is_group=True, is_owner=True, commands="forceflee")
async def cmd_forceflee(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].forceflee(message)


@dp.message_handler(is_group=True, is_owner=True, commands=["killgame", "killgaym"])
async def cmd_killgame(message: types.Message) -> None:
    group_id = int(message.get_args() or message.chat.id)
    if group_id in GAMES:
        GAMES[group_id].state = GameState.KILLGAME
        await asyncio.sleep(2)
        if group_id in GAMES:
            del GAMES[group_id]
            await message.reply("Game ended forcibly.")


@dp.message_handler(is_group=True, is_owner=True, commands="forceskip")
async def cmd_forceskip(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES and GAMES[group_id].state == GameState.RUNNING and not GAMES[group_id].answered:
        GAMES[group_id].time_left = 0


@dp.message_handler(is_group=True, commands="addvp")
async def addvp(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id not in GAMES:
        return
    if isinstance(GAMES[group_id], EliminationGame):
        await message.reply(
            f"Sorry, [On9Bot](https://t.me/{(await on9bot.me).username}) can't play elimination games.",
            disable_web_page_preview=True,
        )
        return
    await GAMES[group_id].addvp(message)


@dp.message_handler(is_group=True, commands="remvp")
async def remvp(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GAMES:
        await GAMES[group_id].remvp(message)


@dp.message_handler(is_group=True, is_owner=True, commands="incmaxp")
async def cmd_incmaxp(message: types.Message) -> None:
    # Thought this could be useful when I implemented this
    # Nope
    group_id = message.chat.id
    if (
            group_id not in GAMES
            or GAMES[group_id].state != GameState.JOINING
            or GAMES[group_id].max_players == GameSettings.INCREASED_MAX_PLAYERS
    ):
        return
    GAMES[group_id].max_players = GameSettings.INCREASED_MAX_PLAYERS
    await message.reply(
        "Max players for this game increased from "
        f"{GAMES[group_id].max_players} to {GameSettings.INCREASED_MAX_PLAYERS}."
    )


@dp.message_handler(is_owner=True, commands="maintmode")
async def cmd_maintmode(message: types.Message) -> None:
    global MAINT_MODE
    MAINT_MODE = not MAINT_MODE
    await message.reply(f"Maintenance mode has been switched {'on' if MAINT_MODE else 'off'}.")


@dp.message_handler(is_group=True, is_owner=True, commands="leave")
async def cmd_leave(message: types.Message) -> None:
    await message.chat.leave()


@dp.message_handler(commands=["stat", "stats", "stalk"])
async def cmd_stats(message: types.Message) -> None:
    rmsg = message.reply_to_message
    if message.chat.id < 0 and not message.get_command().partition("@")[2]:
        return

    user = (rmsg.forward_from or rmsg.from_user) if rmsg else message.from_user
    async with pool.acquire() as conn:
        res = await conn.fetchrow("SELECT * FROM player WHERE user_id = $1;", user.id)

    if not res:
        await message.reply(
            f"No statistics for {user.get_mention(as_html=True)}!",
            parse_mode=types.ParseMode.HTML,
        )
        return

    mention = user.get_mention(
        name=user.full_name + (" \u2b50\ufe0f" if await has_star(user.id) else ""),
        as_html=True,
    )
    text = f"\U0001f4ca Statistics for {mention}:\n"
    text += f"<b>{res['game_count']}</b> games played\n"
    text += f"<b>{res['win_count']} ({res['win_count'] / res['game_count']:.0%})</b> games won\n"
    text += f"<b>{res['word_count']}</b> total words played\n"
    text += f"<b>{res['letter_count']}</b> total letters played\n"
    if res["longest_word"]:
        text += f"Longest word: <b>{res['longest_word'].capitalize()}</b>"
    await message.reply(text.rstrip(), parse_mode=types.ParseMode.HTML)


@dp.message_handler(commands="groupstats")
async def cmd_groupstats(message: types.Message) -> None:  # TODO: Add top players in group (up to 5?)
    if message.chat.id > 0:
        await groups_only_command(message)
        return

    async with pool.acquire() as conn:
        player_cnt, game_cnt, word_cnt, letter_cnt = await conn.fetchrow(
            """\
            SELECT COUNT(DISTINCT user_id), COUNT(DISTINCT game_id), SUM(word_count), SUM(letter_count)
                FROM gameplayer
                WHERE group_id = $1;""",
            message.chat.id,
        )
    await message.reply(
        (
            f"\U0001f4ca Statistics for <b>{quote_html(message.chat.title)}</b>\n"
            f"<b>{player_cnt}</b> players\n"
            f"<b>{game_cnt}</b> games played\n"
            f"<b>{word_cnt}</b> total words played\n"
            f"<b>{letter_cnt}</b> total letters played"
        ),
        parse_mode=types.ParseMode.HTML,
    )


@cached(ttl=5)
async def get_global_stats() -> str:
    async with pool.acquire() as conn:
        group_cnt, game_cnt = await conn.fetchrow(
            "SELECT COUNT(DISTINCT group_id), COUNT(*) FROM game;"
        )
        player_cnt, word_cnt, letter_cnt = await conn.fetchrow(
            "SELECT COUNT(*), SUM(word_count), SUM(letter_count) FROM player;"
        )
    return (
        "\U0001f4ca Global statistics\n"
        f"*{group_cnt}* groups\n"
        f"*{player_cnt}* players\n"
        f"*{game_cnt}* games played\n"
        f"*{word_cnt}* total words played\n"
        f"*{letter_cnt}* total letters played"
    )


@dp.message_handler(commands="globalstats")
async def cmd_globalstats(message: types.Message) -> None:
    await message.reply(await get_global_stats())


@dp.message_handler(is_owner=True, commands=["trend", "trends"])
async def cmd_trends(message: types.Message) -> None:  # TODO: Optimize
    try:
        days = int(message.get_args() or 7)
        assert days > 1, "smh"
    except (ValueError, AssertionError) as e:
        await message.reply(f"`{e.__class__.__name__}: {str(e)}`")
        return

    d = datetime.now().date()
    tp = [d - timedelta(days=i) for i in range(days - 1, -1, -1)]
    f = DateFormatter("%b %d" if days < 180 else "%b" if days < 335 else "%b %Y")

    async def get_daily_games() -> Dict[str, Any]:
        async with pool.acquire() as conn:
            return dict(
                await conn.fetch(
                    """\
                    SELECT start_time::DATE d, COUNT(start_time::DATE)
                        FROM game
                        WHERE start_time::DATE >= $1
                        GROUP BY d
                        ORDER BY d;""",
                    d - timedelta(days=days - 1),
                )
            )

    async def get_active_players() -> Dict[str, Any]:
        async with pool.acquire() as conn:
            return dict(
                await conn.fetch(
                    """\
                    SELECT game.start_time::DATE d, COUNT(DISTINCT gameplayer.user_id)
                        FROM gameplayer
                        INNER JOIN game ON gameplayer.game_id = game.id
                        WHERE game.start_time::DATE >= $1
                        GROUP BY d
                        ORDER BY d;""",
                    d - timedelta(days=days - 1),
                )
            )

    async def get_active_groups() -> Dict[str, Any]:
        async with pool.acquire() as conn:
            return dict(
                await conn.fetch(
                    """\
                    SELECT start_time::DATE d, COUNT(DISTINCT group_id)
                        FROM game
                        WHERE game.start_time::DATE >= $1
                        GROUP BY d
                        ORDER BY d;""",
                    d - timedelta(days=days - 1),
                )
            )

    async def get_cumulative_groups() -> Dict[str, Any]:
        async with pool.acquire() as conn:
            return dict(
                await conn.fetch(
                    """\
                    SELECT *
                        FROM (
                            SELECT d, SUM(count) OVER (ORDER BY d)
                                FROM (
                                    SELECT d, COUNT(group_id)
                                        FROM (
                                            SELECT DISTINCT group_id, MIN(start_time::DATE) d
                                                FROM game
                                                GROUP BY group_id
                                        ) gd
                                        GROUP BY d
                                ) dg
                        ) ds
                        WHERE d >= $1;""",
                    d - timedelta(days=days - 1),
                )
            )

    daily_games, active_players, active_groups, cumulative_groups = await asyncio.gather(
        get_daily_games(), get_active_players(), get_active_groups(), get_cumulative_groups()
    )

    # TODO: Figure out what this does
    async with pool.acquire() as conn:
        dt = d - timedelta(days=days)
        for i in range(days):
            dt += timedelta(days=1)
            if dt not in cumulative_groups:
                if not i:
                    cumulative_groups[dt] = await conn.fetchval(
                        "SELECT COUNT(DISTINCT group_id) FROM game WHERE start_time::DATE <= $1;",
                        dt,
                    )
                else:
                    cumulative_groups[dt] = cumulative_groups[dt - timedelta(days=1)]
        cumulative_players = dict(
            await conn.fetch(
                """\
                SELECT *
                    FROM (
                        SELECT d, SUM(count) OVER (ORDER BY d)
                            FROM (
                                SELECT d, COUNT(user_id)
                                    FROM (
                                        SELECT DISTINCT user_id, MIN(start_time::DATE) d
                                            FROM gameplayer
                                            INNER JOIN game ON game_id = game.id
                                            GROUP BY user_id
                                    ) ud
                                    GROUP BY d
                            ) du
                    ) ds
                    WHERE d >= $1;""",
                d - timedelta(days=days - 1),
            )
        )
        dt = d - timedelta(days=days)
        for i in range(days):
            dt += timedelta(days=1)
            if dt not in cumulative_players:
                if not i:
                    cumulative_players[dt] = await conn.fetchval(
                        """\
                        SELECT COUNT(DISTINCT user_id)
                            FROM gameplayer
                            INNER JOIN game ON game_id = game.id
                            WHERE start_time <= $1;""",
                        dt,
                    )
                else:
                    cumulative_players[dt] = cumulative_players[dt - timedelta(days=1)]
        game_mode_play_cnt = await conn.fetch(
            """\
            SELECT COUNT(game_mode), game_mode
                FROM game
                WHERE start_time::DATE >= $1
                GROUP BY game_mode
                ORDER BY count;""",
            d - timedelta(days=days - 1),
        )
    total_games = sum(i[0] for i in game_mode_play_cnt)

    while os.path.exists("trends.jpg"):  # Another /trend command has not finished processing
        await asyncio.sleep(0.1)

    plt.figure(figsize=(15, 8))
    plt.subplots_adjust(hspace=0.4)
    plt.suptitle(f"Trends in the Past {days} Days", size=25)

    # Draw the 6 subplots

    sp = plt.subplot(231)
    sp.xaxis.set_major_formatter(f)
    sp.yaxis.set_major_locator(MaxNLocator(integer=True))  # Force y-axis intervals to be integral
    plt.setp(sp.xaxis.get_majorticklabels(), rotation=45, horizontalalignment="right")
    plt.title("Games Played", size=18)
    plt.plot(tp, [daily_games.get(i, 0) for i in tp])
    plt.ylim(ymin=0)

    sp = plt.subplot(232)
    sp.xaxis.set_major_formatter(f)
    sp.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.setp(sp.xaxis.get_majorticklabels(), rotation=45, horizontalalignment="right")
    plt.title("Active Groups", size=18)
    plt.plot(tp, [active_groups.get(i, 0) for i in tp])
    plt.ylim(ymin=0)

    sp = plt.subplot(233)
    sp.xaxis.set_major_formatter(f)
    sp.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.setp(sp.xaxis.get_majorticklabels(), rotation=45, horizontalalignment="right")
    plt.title("Active Players", size=18)
    plt.plot(tp, [active_players.get(i, 0) for i in tp])
    plt.ylim(ymin=0)

    plt.subplot(234)
    labels = [i[1] for i in game_mode_play_cnt]
    colors = [
                 "dark maroon", "dark peach", "orange", "leather", "mustard", "teal", "french blue", "booger"
             ][8 - len(game_mode_play_cnt):]
    slices, text = plt.pie(
        [i[0] for i in game_mode_play_cnt],
        labels=[
            f"{i[0] / total_games:.1%} ({i[0]})" if i[0] / total_games >= 0.03 else "" for i in game_mode_play_cnt
        ],
        colors=["xkcd:" + c for c in colors],
        startangle=90,
    )
    plt.legend(slices, labels, title="Game Modes Played", fontsize="x-small", loc="best")
    plt.axis("equal")

    sp = plt.subplot(235)
    sp.xaxis.set_major_formatter(f)
    sp.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.setp(sp.xaxis.get_majorticklabels(), rotation=45, horizontalalignment="right")
    plt.title("Cumulative Groups", size=18)
    plt.plot(tp, [cumulative_groups[i] for i in tp])

    sp = plt.subplot(236)
    sp.xaxis.set_major_formatter(f)
    sp.yaxis.set_major_locator(MaxNLocator(integer=True))
    plt.setp(sp.xaxis.get_majorticklabels(), rotation=45, horizontalalignment="right")
    plt.title("Cumulative Players", size=18)
    plt.plot(tp, [cumulative_players[i] for i in tp])

    # Save the plot as a jpg and send it

    plt.savefig("trends.jpg", bbox_inches="tight")
    plt.close("all")
    async with aiofiles.open("trends.jpg", "rb") as f:
        await message.reply_photo(f)
    await aiofiles.os.remove("trends.jpg")


@dp.message_handler(commands="donate")
async def cmd_donate(message: types.Message) -> None:
    if message.chat.id < 0:
        await message.reply(
            "Slide into my DMs to donate!",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            "Donate in private",
                            url="https://t.me/on9wordchainbot?start=donate",
                        )
                    ]
                ]
            ),
        )
        return
    arg = message.get_args()
    if not arg:
        await send_donate_msg(message)
    else:
        try:
            amt = int(Decimal(arg).quantize(Decimal("1.00")) * 100)
            assert amt > 0
            await send_donate_invoice(message.chat.id, amt)
        except (ValueError, InvalidOperation, AssertionError):
            await message.reply("Invalid amount.\nPlease enter a positive number.")
        except BadRequest as e:
            if str(e) == "Currency_total_amount_invalid":
                await message.reply(
                    "Sorry, the entered amount was not in range (1-10000). " "Please try another amount."
                )
                return
            raise


async def send_donate_msg(message: types.Message) -> None:
    await message.reply(
        "Donate to support this project! \u2764\ufe0f\n"
        "Donations are accepted in HKD (1 USD ≈ 7.75 HKD).\n"
        "Select one of the following options or type in the desired amount in HKD (e.g. `/donate 42.42`).\n\n"
        "Donation rewards:\n"
        "Any amount: \u2b50\ufe0f is displayed next to your name during games.\n"
        "10 HKD (cumulative): Search words in inline queries (e.g. `@on9wordchainbot test`)\n"
        "30 HKD (cumulative): Start mixed elimination games (`/startmelim`)\n",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton("10 HKD", callback_data="donate:10"),
                    types.InlineKeyboardButton("20 HKD", callback_data="donate:20"),
                    types.InlineKeyboardButton("30 HKD", callback_data="donate:30"),
                ],
                [
                    types.InlineKeyboardButton("50 HKD", callback_data="donate:50"),
                    types.InlineKeyboardButton("100 HKD", callback_data="donate:100"),
                ],
            ]
        ),
    )


async def send_donate_invoice(user_id: int, amt: int) -> None:
    await bot.send_invoice(
        chat_id=user_id,
        title="On9 Word Chain Bot Donation",
        description="Support bot development",
        payload=f"on9wordchainbot_donation:{user_id}",
        provider_token=PROVIDER_TOKEN,
        start_parameter="donate",
        currency="HKD",
        prices=[types.LabeledPrice("Donation", amt)],
    )


@dp.pre_checkout_query_handler()
async def pre_checkout_query_handler(pre_checkout_query: types.PreCheckoutQuery) -> None:
    if pre_checkout_query.invoice_payload == f"on9wordchainbot_donation:{pre_checkout_query.from_user.id}":
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    else:
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Donation unsuccessful. No payment was carried out. Mind trying again later? :D",
        )


@dp.message_handler(content_types=ContentTypes.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: types.Message) -> None:
    payment = message.successful_payment
    donation_id = str(uuid4())[:8]
    amt = Decimal(payment.total_amount) / 100
    dt = datetime.now().replace(microsecond=0)
    async with pool.acquire() as conn:
        await conn.execute(
            """\
            INSERT INTO donation (
                donation_id, user_id, amount, donate_time,
                telegram_payment_charge_id, provider_payment_charge_id
            )
            VALUES
                ($1, $2, $3::NUMERIC, $4, $5, $6);""",
            donation_id,
            message.from_user.id,
            str(amt),
            dt,
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id,
        )
    await asyncio.gather(
        message.answer(
            (
                f"Your donation of {amt} HKD is successful.\n"
                "Thank you for your support! :D\n"
                f"Donation id: #on9wcbot_{donation_id}"
            ),
            parse_mode=types.ParseMode.HTML,
        ),
        send_admin_group(
            (
                f"Received donation of {amt} HKD from {message.from_user.get_mention(as_html=True)} "
                f"(id: <code>{message.from_user.id}</code>).\n"
                f"Donation id: #on9wcbot_{donation_id}"
            ),
            parse_mode=types.ParseMode.HTML,
        )
    )


@dp.message_handler(is_owner=True, commands="sql")
async def cmd_sql(message: types.Message) -> None:
    try:
        async with pool.acquire() as conn:
            res = await conn.fetch(message.get_full_command()[1])
    except Exception as e:
        await message.reply(f"`{e.__class__.__name__}: {str(e)}`")
        return

    if not res:
        await message.reply("No results returned.")
        return

    text = ["*" + " - ".join(res[0].keys()) + "*"]
    for r in res:
        text.append("`" + " - ".join([str(i) for i in r.values()]) + "`")
    await message.reply("\n".join(text))


@dp.message_handler(commands=["reqaddword", "reqaddwords"])
async def cmd_reqaddword(message: types.Message) -> None:
    if message.forward_from:
        return

    words_to_add = [w for w in set(message.get_args().lower().split()) if all(c in ascii_lowercase for c in w)]
    if not words_to_add:
        await message.reply(
            "Function: Request addition of new words. Check @on9wcwa for new words.\n"
            "Please check the spelling of words before requesting so I can process your requests faster.\n"
            "Proper nouns are not accepted.\n"
            "Usage: `/reqaddword wordone wordtwo ...`"
        )
        return

    existing = []
    rejected = []
    rejected_with_reason = []
    for w in words_to_add[:]:  # Iterate through a copy so removal of elements is possible
        if check_word_existence(w):
            existing.append("_" + w.capitalize() + "_")
            words_to_add.remove(w)

    async with pool.acquire() as conn:
        rej = await conn.fetch("SELECT word, reason FROM wordlist WHERE NOT accepted;")
    for word, reason in rej:
        if word not in words_to_add:
            continue
        words_to_add.remove(word)
        word = "_" + word.capitalize() + "_"
        if reason:
            rejected_with_reason.append((word, reason))
        else:
            rejected.append(word)

    text = ""
    if words_to_add:
        text += f"Submitted {', '.join(['_' + w.capitalize() + '_' for w in words_to_add])} for approval.\n"
        await send_admin_group(
            message.from_user.get_mention(
                name=message.from_user.full_name + (" \u2b50\ufe0f" if await has_star(message.from_user.id) else ""),
                as_html=True,
            )
            + " is requesting the addition of "
            + ", ".join(["<i>" + w.capitalize() + "</i>" for w in words_to_add])
            + " to the word list. #reqaddword",
            parse_mode=types.ParseMode.HTML,
        )
    if existing:
        text += f"{', '.join(existing)} {'is' if len(existing) == 1 else 'are'} already in the word list.\n"
    if rejected:
        text += f"{', '.join(rejected)} {'was' if len(rejected) == 1 else 'were'} rejected.\n"
    for word, reason in rejected_with_reason:
        text += f"{word} was rejected due to {reason}.\n"
    await message.reply(text.rstrip())


@dp.message_handler(is_owner=True, commands=["addword", "addwords"])
async def cmd_addwords(message: types.Message) -> None:
    words_to_add = [w for w in set(message.get_args().lower().split()) if all(c in ascii_lowercase for c in w)]
    if not words_to_add:
        return
    existing = []
    rejected = []
    rejected_with_reason = []
    for w in words_to_add[:]:  # Cannot iterate while deleting
        if check_word_existence(w):
            existing.append("_" + w.capitalize() + "_")
            words_to_add.remove(w)
    async with pool.acquire() as conn:
        rej = await conn.fetch("SELECT word, reason FROM wordlist WHERE NOT accepted;")
    for word, reason in rej:
        if word not in words_to_add:
            continue
        words_to_add.remove(word)
        word = "_" + word.capitalize() + "_"
        if reason:
            rejected_with_reason.append((word, reason))
        else:
            rejected.append(word)
    text = ""
    if words_to_add:
        async with pool.acquire() as conn:
            await conn.copy_records_to_table("wordlist", records=[(w, True, None) for w in words_to_add])
        text += f"Added {', '.join(['_' + w.capitalize() + '_' for w in words_to_add])} to the word list.\n"
    if existing:
        text += f"{', '.join(existing)} {'is' if len(existing) == 1 else 'are'} already in the word list.\n"
    if rejected:
        text += f"{', '.join(rejected)} {'was' if len(rejected) == 1 else 'were'} rejected.\n"
    for word, reason in rejected_with_reason:
        text += f"{word} was rejected due to {reason}.\n"
    msg = await message.reply(text.rstrip())
    if not words_to_add:
        return
    await update_words()
    await msg.edit_text(msg.md_text + "\n\nWord list updated.")
    await bot.send_message(
        WORD_ADDITION_CHANNEL_ID,
        f"Added {', '.join(['_' + w.capitalize() + '_' for w in words_to_add])} to the word list.",
        disable_notification=True,
    )


@dp.message_handler(is_owner=True, commands="rejword")
async def cmd_rejword(message: types.Message) -> None:
    arg = message.get_args()
    word, _, reason = arg.partition(" ")
    if not word:
        return
    word = word.lower()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT accepted, reason FROM wordlist WHERE word = $1;", word)
        if r is None:
            await conn.execute(
                "INSERT INTO wordlist (word, accepted, reason) VALUES ($1, false, $2)",
                word,
                reason.strip() or None,
            )
    word = word.capitalize()
    if r is None:
        await message.reply(f"_{word}_ rejected.")
    elif r["accepted"]:
        await message.reply(f"_{word}_ was accepted.")
    elif not r["reason"]:
        await message.reply(f"_{word}_ was already rejected.")
    else:
        await message.reply(f"_{word}_ was already rejected due to {r['reason']}.")


@dp.message_handler(commands="feedback")
async def cmd_feedback(message: types.Message) -> None:
    rmsg = message.reply_to_message
    if (
            message.chat.id < 0
            and not message.get_command().partition("@")[2]
            and (not rmsg or rmsg.from_user.id != bot.id)
            or message.forward_from
    ):  # Make sure feedback is directed at this bot
        return

    arg = message.get_full_command()[1]
    if not arg:
        await message.reply(
            "Function: Send feedback to my owner.\n"
            "Usage: `/feedback@on9wordchainbot feedback`"
        )
        return

    await asyncio.gather(
        message.forward(ADMIN_GROUP_ID),
        message.reply("Feedback sent successfully."),
    )


@dp.message_handler(is_group=True, regexp=r"^\w+$")
@dp.edited_message_handler(is_group=True, regexp=r"^\w+$")
async def message_handler(message: types.Message) -> None:
    group_id = message.chat.id
    if (
            group_id in GAMES
            and GAMES[group_id].players_in_game
            and message.from_user.id == GAMES[group_id].players_in_game[0].user_id
            and not GAMES[group_id].answered
            and GAMES[group_id].accepting_answers
            # TODO: Modify to support other languages
            and all([c in ascii_lowercase for c in message.text.lower()])
    ):
        await GAMES[group_id].handle_answer(message)


@dp.inline_handler()
async def inline_handler(inline_query: types.InlineQuery):
    text = inline_query.query.lower()
    if not text or inline_query.from_user.id not in VIP and (await amt_donated(inline_query.from_user.id)) < 10:
        await inline_query.answer(
            [
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a classic game",
                    description="/startclassic@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startclassic@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a hard mode game",
                    description="/starthard@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/starthard@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a chaos game",
                    description="/startchaos@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startchaos@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a chosen first letter game",
                    description="/startcfl@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startcfl@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a banned letters game",
                    description="/startbl@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startbl@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start a required letter game",
                    description="/startrl@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startrl@on9wordchainbot"),
                ),
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start an elimination game",
                    description="/startelim@on9wordchainbot",
                    input_message_content=types.InputTextMessageContent("/startelim@on9wordchainbot"),
                ),
            ],
            is_personal=not text,
        )
        return

    if any(c not in ascii_lowercase for c in text):
        await inline_query.answer(
            [
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="A query can only consist of alphabets",
                    description="Try a different query",
                    input_message_content=types.InputTextMessageContent(r"¯\\_(ツ)\_/¯"),
                )
            ],
            is_personal=True,
        )
        return

    res = []
    for i in filter_words(starting_letter=text[0]):
        if i.startswith(text):
            i = i.capitalize()
            res.append(
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=i,
                    input_message_content=types.InputTextMessageContent(i),
                )
            )
            if len(res) == 50:  # Max 50 results
                break
    if not res:  # No results
        res.append(
            types.InlineQueryResultArticle(
                id=str(uuid4()),
                title="No results found",
                description="Try a different query",
                input_message_content=types.InputTextMessageContent(r"¯\\_(ツ)\_/¯"),
            )
        )
    await inline_query.answer(res, is_personal=True)


@dp.callback_query_handler()
async def callback_query_handler(callback_query: types.CallbackQuery) -> None:
    text = callback_query.data
    if text.startswith("donate"):
        await send_donate_invoice(callback_query.from_user.id, int(text.split(":")[1]) * 100)
    await callback_query.answer()


@dp.errors_handler(exception=Exception)
async def error_handler(update: types.Update, error: TelegramAPIError) -> None:
    for game in GAMES.values():  # TODO: Do this for group in which error occurs only
        asyncio.create_task(game.scan_for_stale_timer())

    if isinstance(error, MigrateToChat):
        if update.message.chat.id in GAMES:  # TODO: Test
            old_gid = GAMES[update.message.chat.id].group_id
            GAMES[error.migrate_to_chat_id] = GAMES.pop(update.message.chat.id)
            GAMES[error.migrate_to_chat_id].group_id = error.migrate_to_chat_id
            asyncio.create_task(
                send_admin_group(f"Game moved from {old_gid} to {error.migrate_to_chat_id}.")
            )
        async with pool.acquire() as conn:
            await conn.execute(
                """\
                UPDATE game SET group_id = $1 WHERE group_id = $2;
                UPDATE gameplayer SET group_id = $1 WHERE group_id = $2;
                DELETE FROM game WHERE group_id = $2;
                DELETE FROM gameplayer WHERE group_id = $2;""",
                error.migrate_to_chat_id,
                update.message.chat.id,
            )
        await send_admin_group(f"Group migrated to {error.migrate_to_chat_id}.")
        return

    send_admin_msg = await send_admin_group(
        f"`{error.__class__.__name__} @ "
        f"{update.message.chat.id if update.message and update.message.chat else 'idk'}`:\n"
        f"`{str(error)}`",
    )
    if not update.message or not update.message.chat:
        return

    try:
        await update.message.reply("Error occurred. My owner has been notified.")
    except TelegramAPIError:
        pass

    if update.message.chat.id in GAMES:
        asyncio.create_task(
            send_admin_msg.reply(f"Killing game in {update.message.chat.id} consequently.")
        )
        GAMES[update.message.chat.id].state = GameState.KILLGAME
        await asyncio.sleep(2)
        try:
            del GAMES[update.message.chat.id]
            await update.message.reply("Game ended forcibly.")
        except:
            pass


def main() -> None:
    executor.start_polling(dp, skip_updates=True)


if __name__ == "__main__":
    main()
