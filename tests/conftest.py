import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
os.environ.setdefault('SIGNAL_BOT_TOKEN','x')
os.environ.setdefault('PROFIT_BOT_TOKEN','x')
os.environ.setdefault('REPORT_BOT_TOKEN','x')
os.environ.setdefault('ALPACA_API_KEY','x')
os.environ.setdefault('ALPACA_API_SECRET','x')
