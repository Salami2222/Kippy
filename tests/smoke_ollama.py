"""Optional real-model smoke test; uses a temporary database and never logs a meal."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macromellow.agent import tokens
from macromellow.ollama import OllamaClient
from macromellow.umd import save_json
from macromellow.web import DataApp

MESSAGES = [
    'I had a macaroni pizza slice, 2 scoops of cilantro lime rice, 1 chocolate chip cookie and a scoop of mac n cheese',
    'The pizza was from The Y',
    'Actually it was 3 scoops of rice, not 2',
    'Wait, remove the cookie',
    'How many calories are in this?',
]


def main():
    client = OllamaClient()
    status = client.status()
    if not status['model_available']:
        print(json.dumps({'status': 'SKIPPED', 'reason': status.get('error_category', 'model_missing')}))
        return 0
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / 'data'
    with tempfile.TemporaryDirectory(prefix='macromellow-smoke-') as directory:
        root = Path(directory)
        for source in data.glob('*.json'):
            save_json(json.loads(source.read_text()), root / source.name)
        app = DataApp(root, client)
        conversation = None
        for turn, message in enumerate(MESSAGES, 1):
            reply = app.conversations.turn(message, conversation)
            conversation = reply['conversation_id']
            state = app.memory.conversation(conversation)
            error = reply.get('debug', {}).get('error')
            print(json.dumps({'turn': turn, 'kind': reply['kind'], 'food_count': len(state['foods']),
                              'error_category': error.get('category') if error else None}), flush=True)
            if reply['kind'] == 'error':
                return 1
            if turn == 1:
                expected = [('macaroni pizza', 1, 'slice'), ('cilantro lime rice', 2, 'scoop'),
                            ('chocolate chip cookie', 1, 'each'), ('mac n cheese', 1, 'scoop')]
                actual = [(tokens(f['name']), f['quantity'], f['unit']) for f in state['foods']]
                assert actual == [(tokens(n), q, u) for n, q, u in expected], 'Four-food extraction mismatch'
            if turn == 2:
                assert state['foods'][0]['hall'] == 'yahentamitsi', 'Hall correction lost'
            if turn == 3:
                assert len(state['foods']) == 4 and state['foods'][1]['quantity'] == 3, 'Quantity correction lost context'
            if turn >= 4:
                assert len(state['foods']) == 3 and all('cookie' not in tokens(f['name']) for f in state['foods']), 'Cookie removal lost context'
            assert not any(app.memory.tracker()['totals'].values()), 'Lookup unexpectedly changed macros'
            assert not app.memory.meals(), 'Lookup unexpectedly logged a meal'
    print('PASS: live five-turn lookup, corrections, removal, and tracker isolation')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
