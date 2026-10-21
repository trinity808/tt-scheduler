"""
The real client behind app/case_metadata.py: stores and reads developer
metadata on the rows of one Google Sheets tab through the Sheets API.

It sends its requests through the gspread worksheet's own authenticated
client, so it needs no credentials code of its own. It implements the three
operations case_metadata.py expects (search, create, delete), converting
between the API's 0-based row indexes and the 1-based row numbers used
everywhere else.

Every entry is created with DOCUMENT visibility, so any Cloud project with
access to the sheet can read it. PROJECT visibility would make an entry
readable only by the project that wrote it, which would strand every stored
list if the service account ever moved to a different project.

The tab that was created with the spreadsheet has sheetId 0, and the API
leaves that out of its responses (seen on the sandbox's Ongoing tab). A
reader that expected the field would find no entries at all on that tab,
which looks like "nothing stored", so a missing sheetId is read as 0. A
missing startIndex is read as 0 too; the API does send one for row 1, but
tolerating its absence costs nothing.

Entries on other tabs are ignored, as are entries covering more than one
row; this module only ever creates single-row entries.
"""

from gspread.urls import SPREADSHEET_URL

from app.case_metadata import MetadataEntry


VISIBILITY = "DOCUMENT"


class SheetsMetadataClient:
    def __init__(self, worksheet):
        self._http = worksheet.client
        self._spreadsheet_id = worksheet.spreadsheet_id
        self._sheet_id = worksheet.id

    def _batch_update(self, request):
        return self._http.batch_update(self._spreadsheet_id, {"requests": [request]})

    def search_raw(self, key):
        """
        Every row-level entry with this key, across all tabs, exactly as
        the API returns it. search() filters these down; this is also
        handy for checking what the API actually sends back.
        """
        url = SPREADSHEET_URL % self._spreadsheet_id + "/developerMetadata:search"
        body = {
            "dataFilters": [{
                "developerMetadataLookup": {
                    "metadataKey": key,
                    "locationType": "ROW",
                }
            }]
        }
        response = self._http.request("post", url, json=body)
        return response.json().get("matchedDeveloperMetadata", [])

    def search(self, key):
        entries = []
        for match in self.search_raw(key):
            metadata = match["developerMetadata"]
            span = metadata.get("location", {}).get("dimensionRange")
            if not span or span.get("dimension") != "ROWS":
                continue
            if span.get("sheetId", 0) != self._sheet_id:
                continue
            start = span.get("startIndex", 0)
            if span.get("endIndex", 0) - start != 1:
                continue
            entries.append(
                MetadataEntry(
                    metadata_id=metadata["metadataId"],
                    row_number=start + 1,
                    value=metadata.get("metadataValue", ""),
                )
            )
        return entries

    def create(self, row_number, key, value):
        self._batch_update({
            "createDeveloperMetadata": {
                "developerMetadata": {
                    "metadataKey": key,
                    "metadataValue": value,
                    "visibility": VISIBILITY,
                    "location": {
                        "dimensionRange": {
                            "sheetId": self._sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_number - 1,
                            "endIndex": row_number,
                        }
                    },
                }
            }
        })

    def delete(self, metadata_id):
        self._batch_update({
            "deleteDeveloperMetadata": {
                "dataFilter": {
                    "developerMetadataLookup": {"metadataId": metadata_id}
                }
            }
        })