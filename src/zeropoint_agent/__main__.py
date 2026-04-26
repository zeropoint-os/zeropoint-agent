"""Entry point: python -m zeropoint_agent"""

import uvicorn
from zeropoint_agent.server import app

uvicorn.run(app, host="0.0.0.0", port=2370, log_config=None)
