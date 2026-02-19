"""
Streaming CSV export helpers for Truff-Admin.
"""

import csv
import io
from django.http import StreamingHttpResponse


class Echo:
    """Pseudo-buffer that returns what is written (for streaming CSV)."""
    def write(self, value):
        return value


def streaming_csv_response(filename, header, row_generator):
    """
    Build a StreamingHttpResponse that writes CSV rows on-the-fly.
    - filename: download file name, e.g. 'bookings_export.csv'
    - header: list of column names
    - row_generator: iterable yielding lists/tuples of values
    """
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)

    def rows():
        yield writer.writerow(header)
        for row in row_generator:
            yield writer.writerow(row)

    response = StreamingHttpResponse(rows(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
