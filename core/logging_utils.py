import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {'level': record.levelname, 'event': getattr(record, 'event', record.getMessage()),
                'message': record.getMessage()}
        for key in ('clip_id', 'source_file'):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)
