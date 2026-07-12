from __future__ import annotations

import discord

ROB_PURPLE = discord.Colour.from_rgb(139, 92, 246)
ROB_GREEN = discord.Colour.from_rgb(34, 197, 94)
ROB_RED = discord.Colour.from_rgb(220, 38, 38)
ROB_BLUE = discord.Colour.from_rgb(59, 130, 246)
ROB_GOLD = discord.Colour.from_rgb(234, 179, 8)
ROB_GREY = discord.Colour.from_rgb(107, 114, 128)

ROB_PINK = discord.Colour.from_rgb(255, 101, 178)
ROB_TEAL = discord.Colour.from_rgb(0, 188, 188)
ROB_DARK = discord.Colour.from_rgb(42, 37, 58)

COLOR_SUCCESS = ROB_GREEN
COLOR_DANGER = ROB_RED
COLOR_INFO = discord.Colour(9_133_302)
COLOR_WARNING = ROB_GOLD
COLOR_PROFILE = ROB_PURPLE
COLOR_EVENT = ROB_PURPLE

PROFILE_COLOR_PRESETS: list[tuple[int, str, str]] = [
    (ROB_PINK.value, "💗", "Pink"),
    (ROB_PURPLE.value, "💜", "Purple"),
    (ROB_RED.value, "🔴", "Red"),
    (ROB_BLUE.value, "🔵", "Blue"),
    (ROB_GREEN.value, "💚", "Green"),
    (ROB_GOLD.value, "🟡", "Gold"),
    (ROB_TEAL.value, "🩵", "Teal"),
    (ROB_DARK.value, "🖤", "Dark"),
]
