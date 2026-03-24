# %%
print("Test Reranking")

# %% [markdown]
# # Steps to be implemented : 
# 
# 1. Use retreival get top chunks 
# 2. pass it to the reranker 
# 3. check the outputs at each stage along with scores 

# %%
# Add project root to path
from pathlib import Path
import sys
sys.path.insert(0, str(Path('.').resolve().parent))

# %%
from src.retrieval.models import QueryRequest, QueryResponse, RankedChunk
from src.retrieval.normalizer import build_cache_key
from src.retrieval.qdrant_retriever import QdrantRetriever
from src.retrieval.reranker import get_reranker
from src.config.settings import get_ingestion_settings
settings =  get_ingestion_settings()

# %%
#query embeddings
from src.ingestion.embedder import GeminiEmbedder
from src.retrieval.models import ScoredChunk
finding_test = "MYSQL port 3000 is open publically exposed"

embedder = GeminiEmbedder(settings)
query_embedding = embedder.embed_query(finding_test)
    # logger.info("Embedded finding (%d dims)", len(query_embedding))
framework_key = "cis_controls_v8"

# %%


# %%
#retriever to fetch initial results

from qdrant_client import QdrantClient, models
_client = QdrantClient(url=settings.qdrant_url, timeout=30)
retriever = QdrantRetriever(settings)
results = _client.search(
    collection_name=settings.collection_name,
    query_vector=query_embedding,
    query_filter=models.Filter(
        must=[
            models.FieldCondition(
                key="framework",
                match=models.MatchValue(value=framework_key),
            )
        ]
    ),
    limit=30,
    with_payload=True,
)

chunks = []
for hit in results:
    payload = hit.payload or {}
    text = payload.pop("text", "")
    chunks.append(ScoredChunk(
        text=text,
        metadata=payload,
        qdrant_score=hit.score,
    ))

    

# %%
#scores of chunks 
chunks_scores= {c.text: c.qdrant_score for c in chunks}
chunks_scores_sorted = dict(sorted(chunks_scores.items(), key=lambda item: item[1], reverse=True))

# %%
chunks[0]
#combine all the chunk information into string for reranking
# for c in chunks:
#     print(f"Score: {c.qdrant_score}, Text: {c.text}, Metadata: {c.metadata}")

#function to combined and metadata and text in to clean string for reranking
def combine_chunk_info(chunk: ScoredChunk) -> str:
    # metadata_str = ", ".join(f"{k}: {v}" for k, v in chunk.metadata.items())
    return f"{chunk.text}"
rerank_chunk_context = [combine_chunk_info(c) for c in chunks]
# for c in chunks:
#     combined_info = combine_chunk_info(c)
#     print(f"Score: {c.qdrant_score}, Combined Info: {combined_info}")

# %%
rerank_chunk_context[1]

# %%
len(chunks)

# %%
# chunks_scores_sorted

# %%
#data for reranking : query and chunks with metadata and text combined as string
# reranking
rerank_chunk_context

# %%
rerank_chunk_context

# %%
context = """Ensure the violated finding given comes from which evidence chunk << """ + finding_test 

# %%
# context += "  Your task is to map a security finding to specific controls from compliance frameworks"

# %%
cross_data = list(zip([context]*len(rerank_chunk_context), rerank_chunk_context))
cross_data[:2]

# %%
cross_data[0][:]

# %%
cross_data[1][0]

# %%
cross_data[1][1]

# %%
#cross encoder reranking : 
from sentence_transformers import CrossEncoder

model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')
scores = model.predict(cross_data)
print(scores)
# [ 8.607138 -4.320078]


# %%


# %%
scores = model.predict(cross_data)
print(scores)
# import torch
# probabilities = torch.sigmoid(torch.tensor(scores))
# print(probabilities)

# %%
ranks = model.rank(finding_test, rerank_chunk_context, return_documents=True)

# %%
ranks

# %%
# #reranking 
# reranker = get_reranker(settings)
# reranked_chunks = reranker.rerank(query=finding_test, chunks=rerank_chunk_context)

# %%
# reranked_chunks

# %%
cross_data[0]

# %%
# cross_data[:][0][1]
# queries = []
# answers = []
# for i in range(len(cross_data)):
#     print(f"Question: {cross_data[i][0]}")
#     print(f"Answer: {cross_data[i][1]}")
#     queries.append(cross_data[i][0])
#     answers.append(cross_data[i][1])

# %%
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

model = AutoModelForSequenceClassification.from_pretrained('cross-encoder/ms-marco-MiniLM-L6-v2')
tokenizer = AutoTokenizer.from_pretrained('cross-encoder/ms-marco-MiniLM-L6-v2')

features = tokenizer(queries, answers,  padding=True, truncation=True, return_tensors="pt")

# model.eval()
# # print(model.eval())
# with torch.no_grad():
#     scores = model(**features).logits
#     print(scores)


