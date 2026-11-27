"""Explicit canonical store setup for self-owned DLL sources, never a fallback."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def empty_store_source():
    return (ROOT / 'event_probe_store.js').read_text(encoding='utf8') + '''
const ownedCanonicalStore=new ArkdpsEventStore();
__arkdpsPublicationJournal.bindStore(ownedCanonicalStore.view());
'''
