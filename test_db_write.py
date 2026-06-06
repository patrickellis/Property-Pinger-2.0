import sys
import os
sys.path.append(os.path.abspath("src"))
from core.db import mark_evaluated

# Try writing to a dummy property "TEST1234"
print("Calling mark_evaluated...")
mark_evaluated("TEST1234", ignored=True, score=0.0, breakdown={"pros": []})
print("Done!")
