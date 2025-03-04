from __future__ import annotations

import collections
import datetime
import functools
import itertools
import json
import os
import re
import urllib.request
from typing import Counter
from typing import Iterator
from typing import Mapping
from typing import Match
from typing import Pattern
from typing import Sequence

from bot.config import Config
from bot.data import command
from bot.data import esc
from bot.data import format_msg
from bot.permissions import optional_user_arg

CHAT_LOG_RE = re.compile(
    r'^\[[^]]+\][^<*]*(<(?P<chat_user>[^>]+)>|\* (?P<action_user>[^ ]+))',
)
BONKER_RE = re.compile(r'^\[[^]]+\][^<*]*<(?P<chat_user>[^>]+)> !bonk\b')
BONKED_RE = re.compile(r'^\[[^]]+\][^<*]*<[^>]+> !bonk @?(?P<chat_user>\w+)')


@functools.lru_cache(maxsize=None)
def _counts_per_file(filename: str, reg: Pattern[str]) -> Mapping[str, int]:
    counts: Counter[str] = collections.Counter()
    with open(filename, encoding='utf8') as f:
        for line in f:
            match = reg.match(line)
            if match is None:
                assert reg is not CHAT_LOG_RE
                continue
            user = match['chat_user'] or match['action_user']
            assert user, line
            counts[user.lower()] += 1
    return counts


def _chat_rank_counts(reg: Pattern[str]) -> Counter[str]:
    total: Counter[str] = collections.Counter()
    for filename in os.listdir('logs'):
        full_filename = os.path.join('logs', filename)
        if filename != f'{datetime.date.today()}.log':
            total.update(_counts_per_file(full_filename, reg))
        else:
            # don't use the cached version for today's logs
            total.update(_counts_per_file.__wrapped__(full_filename, reg))
    return total


def _tied_rank(
        counts: Sequence[tuple[str, int]],
) -> Iterator[tuple[int, tuple[int, Iterator[tuple[str, int]]]]]:
    # "counts" should be sorted, usually produced by Counter.most_common()
    grouped = itertools.groupby(counts, key=lambda pair: pair[1])
    yield from enumerate(grouped, start=1)


def _user_rank_by_line_type(
        username: str, reg: Pattern[str],
) -> tuple[int, int] | None:
    total = _chat_rank_counts(reg)
    target_username = username.lower()
    for rank, (count, users) in _tied_rank(total.most_common()):
        for username, _ in users:
            if target_username == username:
                return rank, count
    else:
        return None


def _top_n_rank_by_line_type(reg: Pattern[str], n: int = 10) -> list[str]:
    total = _chat_rank_counts(reg)
    user_list = []
    for rank, (count, users) in _tied_rank(total.most_common(n)):
        usernames = ', '.join(username for username, _ in users)
        user_list.append(f'{rank}. {usernames} ({count})')
    return user_list


@functools.lru_cache(maxsize=1)
def _log_start_date() -> str:
    logs_start = min(os.listdir('logs'))
    logs_start, _, _ = logs_start.partition('.')
    return logs_start


@command('!chatrank')
async def cmd_chatrank(config: Config, match: Match[str]) -> str:
    user = optional_user_arg(match)
    ret = _user_rank_by_line_type(user, CHAT_LOG_RE)
    if ret is None:
        return format_msg(match, f'user not found {esc(user)}')
    else:
        rank, n = ret
        return format_msg(
            match,
            f'{esc(user)} is ranked #{rank} with {n} messages '
            f'(since {_log_start_date()})',
        )


@command('!top10chat')
async def cmd_top_10_chat(config: Config, match: Match[str]) -> str:
    top_10_s = ', '.join(_top_n_rank_by_line_type(CHAT_LOG_RE, n=10))
    return format_msg(match, f'{top_10_s} (since {_log_start_date()})')


@command('!bonkrank')
async def cmd_bonkrank(config: Config, match: Match[str]) -> str:
    user = optional_user_arg(match)
    ret = _user_rank_by_line_type(user, BONKER_RE)
    if ret is None:
        return format_msg(match, f'user not found {esc(user)}')
    else:
        rank, n = ret
        return format_msg(
            match,
            f'{esc(user)} is ranked #{rank}, has bonked others {n} times',
        )


@command('!top5bonkers')
async def cmd_top_5_bonkers(config: Config, match: Match[str]) -> str:
    top_5_s = ', '.join(_top_n_rank_by_line_type(BONKER_RE, n=5))
    return format_msg(match, top_5_s)


@command('!bonkedrank')
async def cmd_bonkedrank(config: Config, match: Match[str]) -> str:
    user = optional_user_arg(match)
    ret = _user_rank_by_line_type(user, BONKED_RE)
    if ret is None:
        return format_msg(match, f'user not found {esc(user)}')
    else:
        rank, n = ret
        return format_msg(
            match,
            f'{esc(user)} is ranked #{rank}, has been bonked {n} times',
        )


@command('!top5bonked')
async def cmd_top_5_bonked(config: Config, match: Match[str]) -> str:
    top_5_s = ', '.join(_top_n_rank_by_line_type(BONKED_RE, n=5))
    return format_msg(match, top_5_s)


def lin_regr(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    sum_x = sum(x)
    sum_xx = sum(xi * xi for xi in x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    b = (sum_y * sum_xx - sum_x * sum_xy) / (len(x) * sum_xx - sum_x * sum_x)
    a = (sum_xy - b * sum_x) / sum_xx
    return a, b


@command('!chatplot')
async def cmd_chatplot(config: Config, match: Match[str]) -> str:
    user = optional_user_arg(match).lower()

    min_date = datetime.date.fromisoformat(_log_start_date())
    x: list[int] = []
    y = []

    for filename in sorted(os.listdir('logs')):
        if filename == f'{datetime.date.today()}.log':
            continue

        filename_date = datetime.date.fromisoformat(filename.split('.')[0])

        full_filename = os.path.join('logs', filename)
        counts = _counts_per_file(full_filename, CHAT_LOG_RE)
        if x or counts[user]:
            x.append((filename_date - min_date).days)
            y.append(counts[user])

    if len(x) < 2:
        return format_msg(
            match, f'sorry {esc(user)}, need at least 2 days of data',
        )

    m, c = lin_regr(x, y)

    chart = {
        'type': 'scatter',
        'data': {
            'datasets': [
                {
                    'label': 'chats',
                    'data': [
                        {'x': x_i, 'y': y_i}
                        for x_i, y_i in zip(x, y)
                        if y_i
                    ],
                },
                {
                    'label': 'trend',
                    'type': 'line',
                    'fill': False,
                    'pointRadius': 0,
                    'data': [
                        {'x': x[0], 'y': m * x[0] + c},
                        {'x': x[-1], 'y': m * x[-1] + c},
                    ],
                },
            ],
        },
        'options': {
            'scales': {
                'xAxes': [{'ticks': {'callback': 'CALLBACK'}}],
                'yAxes': [{'ticks': {'beginAtZero': True, 'min': 0}}],
            },
            'title': {
                'display': True,
                'text': f"{user}'s chat in twitch.tv/{config.channel}",
            },
        },
    }

    callback = (
        'x=>{'
        f'y=new Date({str(min_date)!r});'
        'y.setDate(x+y.getDate());return y.toISOString().slice(0,10)'
        '}'
    )
    data = json.dumps(chart, separators=(',', ':'))
    data = data.replace('"CALLBACK"', callback)

    post_data = {'chart': data}
    request = urllib.request.Request(
        'https://quickchart.io/chart/create',
        method='POST',
        data=json.dumps(post_data).encode(),
        headers={'Content-Type': 'application/json'},
    )
    resp = urllib.request.urlopen(request)
    contents = json.load(resp)
    return format_msg(match, f'{esc(user)}: {contents["url"]}')
