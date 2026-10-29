import json
import re


def main(player_id: str, action: str, message: str, request_id: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}', player_id):
        raise ValueError('玩家代号需为 3–64 位字母、数字、下划线或短横线')
    if action not in ['visit', 'prank', 'help', 'apologize', 'buy']:
        raise ValueError('请选择有效动作')
    return {'body': json.dumps({'player_id': player_id, 'request_id': request_id,
                               'action': action, 'message': message or ''}, ensure_ascii=False)}
