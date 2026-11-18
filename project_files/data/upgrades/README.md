# Team upgrades

The upgrades panel reads `data/upgrades/<meeting_key>.json`. The file for the Dutch Grand Prix 2025 is `1267.json`.

No API publishes team upgrades. The only source is the car presentation document that teams submit to the FIA before an event. I copy items from that PDF by hand. Nothing is filled in by the code, and the file ships empty.

Format:

```json
{
  "source": "Title of the FIA car presentation document",
  "items": [
    {"team": "Team name as in the timing tower", "component": "Front wing", "description": "Text copied from the document"}
  ]
}
```

- `source` is the document title. It stays `null` while there are no items.
- Each item has `team`, `component` and `description`.
- With no items the panel shows: "No FIA car presentation document loaded for this event."
