import traceback, sys
sys.path.append('d:/Users/ayebee/source/repos/EPLogger')
from view import app

try:
    with app.test_request_context('/'):
        response = app.full_dispatch_request()
        print(response)
except Exception as e:
    traceback.print_exc()
