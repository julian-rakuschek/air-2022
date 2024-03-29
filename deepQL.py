import torch
from tqdm import tqdm
from util import *
from transformers import T5Config, T5Tokenizer, T5ForConditionalGeneration
from initial_rank import *

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# SOURCE: https://github.com/ielab/DeepQLM-ECIR2021
# Repo for the Paper "Deep Query Likelihood Model for Information Retrieval"
# Authors: Shengyao Zhuang, Hang Li , Guido Zuccon
#
# Code was adapted to fit our needs

def transform_data(text_key, id_key, collection):
    transformed = {}
    for item in collection:
        transformed[int(item[id_key])] = item[text_key]
    return transformed

def transform_run(qrels):
    run = {}
    for item in qrels:
        query_id = item['query_id']
        corpus_id = item['corpus_id']
        if query_id not in run.keys():
            run[str(query_id)] = []
        run[str(query_id)].append(str(corpus_id))
    return run

def perform_rerank(query, tokenizer, model, corpus, sims):
    rerank_cut = 1000
    batch_size = 64
    docids = []
    for sim in sims:
        docids.append(sim[0])
    num_docs = min(rerank_cut, len(docids))  # rerank top k
    num_iter = num_docs // batch_size + 1
    collection = transform_data('text', '_id', corpus)

    total_scores = []
    for i in range(num_iter):
        start = i * batch_size
        end = (i + 1) * batch_size
        if end > num_docs:
            end = num_docs
            if start == end:
                continue

        batch_passages = []
        for docid in docids[start:end]:
            batch_passages.append(collection[docid])

        inputs = tokenizer(batch_passages, return_tensors='pt', padding=True).to(DEVICE)
        labels = tokenizer([query] * (end - start), return_tensors='pt', padding=True).input_ids.to(DEVICE)

        with torch.no_grad():
            logits = model(**inputs, labels=labels, return_dict=True).logits

            distributions = torch.softmax(logits, dim=-1)  # shape[batch_size, decoder_dim, num_tokens]
            decoder_input_ids = labels.unsqueeze(-1)  # shape[batch_size, decoder_dim, 1]
            batch_probs = torch.gather(distributions, 2, decoder_input_ids).squeeze(
                -1)  # shape[batch_size, decoder_dim]
            masked_log_probs = torch.log10(batch_probs)  # shape[batch_size, decoder_dim]
            scores = torch.sum(masked_log_probs, 1)  # shape[batch_size]
            total_scores.append(scores)

    total_scores = torch.cat(total_scores).cpu().numpy()
    zipped_lists = zip(total_scores, docids)
    sorted_pairs = sorted(zipped_lists, reverse=True)
    res = []
    for i in range(num_docs):
        score, docid = sorted_pairs[i]
        res.append((docid, score))

    return res


def process_queries(queries, train_rel, corpus):
    dictionary, lsi, index = create_model(corpus)

    tokenizer = T5Tokenizer.from_pretrained('t5-base', cache_dir="simple-re-ranker/.cache")
    config = T5Config.from_pretrained('t5-base', cache_dir="simple-re-ranker/.cache")
    model = T5ForConditionalGeneration.from_pretrained('t5-base-tf/model.ckpt-1004000', from_tf=True, config=config)
    model.to(DEVICE)
    rr_basic, rr_reranked = [], []
    for query in tqdm(queries, desc="Ranking queries...."):
        sims = run_first_stage_retrieval(query, dictionary, lsi, index)
        rr1 = reciprocal_rank(sims, train_rel, query)
        rerank = perform_rerank(query["text"], tokenizer, model, corpus, sims)
        rr2 = reciprocal_rank(rerank, train_rel, query)
        print(f"[Query {query['_id']}] RR Basic: {rr1}, RR Reranked: {rr2}")
        rr_basic.append(rr1)
        rr_reranked.append(rr2)
    print("-" * 50)
    print(f"MRR Basic: {sum(rr_basic) / len(queries)}")
    print(f"MRR Reranked: {sum(rr_reranked) / len(queries)}")


def main():
    corpus, queries, train_rel = prepare_data()
    process_queries(queries, train_rel, corpus)


if __name__ == '__main__':
    main()
