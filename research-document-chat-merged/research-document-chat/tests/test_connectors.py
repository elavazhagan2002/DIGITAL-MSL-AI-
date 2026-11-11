"""
Automated Unit Tests for PubMed and ClinicalTrials Connectors.
Uses mocked requests.Session to isolate external network calls.
"""
import pytest
from unittest.mock import MagicMock
from connectors.pubmed_client import PubMedClient
from connectors.clinicaltrials_client import ClinicalTrialsClient


def test_pubmed_client_initialization():
    client = PubMedClient(base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils")
    assert client.base_url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def test_pubmed_search_mocked():
    mock_session = MagicMock()
    
    # Mock esearch response
    mock_esearch = MagicMock()
    mock_esearch.status_code = 200
    mock_esearch.json.return_value = {
        "esearchresult": {
            "count": "1",
            "retmax": "1",
            "retstart": "0",
            "idlist": ["38123456"]
        }
    }

    mock_session.request.return_value = mock_esearch

    client = PubMedClient(base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils", session=mock_session)
    pmids = client.search(["pembrolizumab"], retmax=1)

    assert len(pmids) == 1
    assert pmids[0] == "38123456"


def test_clinicaltrials_search_mocked():
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT03812345",
                        "briefTitle": "Study of Pembrolizumab in Lung Cancer"
                    },
                    "descriptionModule": {
                        "briefSummary": "A phase 3 randomized clinical study."
                    },
                    "statusModule": {
                        "overallStatus": "RECRUITING",
                        "startDateStruct": {"startDate": "2023-01"}
                    }
                }
            }
        ]
    }
    mock_session.get.return_value = mock_response

    client = ClinicalTrialsClient(base_url="https://clinicaltrials.gov/api/v2", session=mock_session)
    records = client.search(["Pembrolizumab"], page_size=1)

    assert len(records) == 1
    assert records[0].nct_id == "NCT03812345"
    assert "Pembrolizumab" in records[0].title
