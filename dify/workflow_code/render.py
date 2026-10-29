import json
import re


def main(game: str, body: str) -> dict:
    data = json.loads(game)
    narration = ''
    try:
        result = json.loads(body)
        narration = result.get('response', '') if result.get('done') else ''
        if not isinstance(narration, str):
            narration = ''
        narration = re.sub(r'<think>.*?</think>', '', narration, flags=re.S).strip()[:1800]
    except (ValueError, TypeError):
        pass
    mode = 'AI 店主对白（游戏数值以账本为准）'
    if not narration:
        narration = data['fallback']
        mode = '模型暂不可用，使用规则对白；本轮状态已写入 seekdb'
    s = data['state']
    inventory = '、'.join(s['inventory']) or '还没有，货架上的怪东西在等你'
    recalled = '\n'.join('- 第 %s 回合：%s' % (m['turn'], m['memory']) for m in data['memories']) or '- 首次来店，账本里还没有旧事。'
    answer = ('# 记仇杂货铺\n\n> ' + narration.replace('\n', '\n> ') + '\n\n'
              + '### 本轮账单\n' + data['outcome'] + '\n\n'
              + '| 回合 | 金币 | 信任 | 下一件价格 |\n| --- | --- | --- | --- |\n'
              + '| %s | %s | %s | %s |\n\n' % (s['turn'], s['coins'], s['trust'], s['next_price'])
              + '**背包：** ' + inventory + '\n\n**目标：** ' + s['ending'] + '\n\n'
              + '### 店主翻出的旧账（来自 seekdb）\n' + recalled + '\n\n'
              + '下次可选：visit 进店看看 / prank 恶作剧（+3 金币、-2 信任） / help 帮忙（+5 金币、+1 信任） / '
              + 'apologize 道歉（-5 金币、+2 信任） / buy 买怪东西。信任限制为 -5 至 5。\n\n'
              + '保持相同玩家代号即可跨次继续；更换新代号开始新存档。\n\n' + mode)
    return {'answer': answer}
