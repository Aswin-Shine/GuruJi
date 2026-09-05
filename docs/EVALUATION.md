# Evaluation runs — raw harness output

Verbatim terminal output from every recorded run of `backend/scripts/eval_retrieval.py`,
kept unedited so numbers quoted elsewhere can be traced to the run that produced them.

Read [EVALUATION.md](EVALUATION.md) first for metric definitions and the caveats that
apply to all of these — in particular that the evaluation set is 82% Class 8.

Runs appear oldest first. Corpus size grows through the file as classes were ingested
in the order 8, 9, 10, 7, 6, 5. The final section, "Last Eval Setting", is the current
recorded state.

---

# Class 8 - Baseline 

## VERBOSE 
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-c4793fee65ce Creating 
Container guruji-api-run-c4793fee65ce Created 
Corpus: 653 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.1%   (target >80%)   n=103
MRR             0.951   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    95.1%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  94.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2069 ms

kind                     score    n
adjacent                  92%   12
direct                    96%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             95%   41
typo                     100%    1
vocab_free               100%   18

--- 2 retrieval misses (wrong chapter) ---
[direct] 'What is a hypothesis in science?'
    rewritten -> 'scientific hypothesis testable explanation'
    wanted ch.1, got (class, ch) [(8, 2), (8, 2), (8, 2), (8, 2), (8, 8)] scores [0.346, 0.342, 0.336, 0.312, 0.305]
[adjacent] 'namak aur reth ko alag kaise karein'
    rewritten -> 'separation of salt and sand mixture dissolution filtration evaporation'
    wanted ch.8, got (class, ch) [(8, 9), (8, 9), (8, 9), (8, 9), (8, 9)] scores [0.499, 0.48, 0.468, 0.449, 0.447]

--- 2 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'human digestive system ke parts'
    rewritten -> 'human digestive system parts organs and functions'
    grounded on ch[2, 2, 2] at [0.479] (threshold 0.4)

## SWEEP 
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --sweep 
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-5f2b2436df29 Creating 
Container guruji-api-run-5f2b2436df29 Created 
Corpus: 653 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      99.0%   (target >80%)   n=103
MRR             0.949   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    95.1%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  93.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2433 ms

kind                     score    n
adjacent                  92%   12
direct                   100%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             95%   41
typo                     100%    1
vocab_free               100%   18

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   99.0%   0.949    87.8%     99.0%
  0.20      0.40   99.0%   0.949    95.1%     93.2%
  0.20      0.45   99.0%   0.949    97.6%     86.4%
  0.25      0.35   99.0%   0.949    87.8%     99.0%
  0.25      0.40   99.0%   0.949    95.1%     93.2%
  0.25      0.45   99.0%   0.949    97.6%     86.4%
  0.28      0.35   99.0%   0.949    87.8%     99.0%
  0.28      0.40   99.0%   0.949    95.1%     93.2%
  0.28      0.45   99.0%   0.949    97.6%     86.4%
  0.30      0.35   99.0%   0.949    87.8%     99.0%
  0.30      0.40   99.0%   0.949    95.1%     93.2%
  0.30      0.45   99.0%   0.949    97.6%     86.4%
  0.35      0.35   98.1%   0.939    87.8%     99.0%
  0.35      0.40   98.1%   0.939    95.1%     93.2%
  0.35      0.45   98.1%   0.939    97.6%     86.4%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate. 

## NO-REWRITE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --no-rewrite
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-c348a9ad954d Creating 
Container guruji-api-run-c348a9ad954d Created 
Corpus: 653 chunks. Eval set: 158 rows. Query rewrite: OFF (pre-fix baseline). Lexical rescue: ON

Recall@5      71.8%   (target >80%)   n=103
MRR             0.682   (target >0.70)
Gate accuracy   0.0%   (non-questions correctly skipped)   n=14
Refusal acc.    92.7%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  49.5%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   868 ms

kind                     score    n
adjacent                  50%   12
direct                   100%   24
followup                  12%    8
hinglish                  95%   40
nonsearch/gate             0%   14
out_of_corpus             93%   41
typo                     100%    1
vocab_free                22%   18

---

# Class 9 - Compare

## VERBOSE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose   
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-3351ec2d6dff Creating 
Container guruji-api-run-3351ec2d6dff Created 
Corpus: 1677 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.1%   (target >80%)   n=103
MRR             0.951   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    95.1%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  93.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2320 ms

kind                     score    n
adjacent                  92%   12
direct                    96%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             95%   41
typo                     100%    1
vocab_free               100%   18

--- 2 retrieval misses (wrong chapter) ---
[direct] 'What is a hypothesis in science?'
    rewritten -> 'scientific hypothesis testable explanation'
    wanted ch.1, got (class, ch) [(8, 2), (8, 2), (8, 2), (8, 2), (8, 8)] scores [0.346, 0.342, 0.336, 0.312, 0.305]
[adjacent] 'namak aur reth ko alag kaise karein'
    rewritten -> 'separation of salt and sand mixture dissolving filtration evaporation'
    wanted ch.8, got (class, ch) [(8, 9), (8, 9), (8, 9), (8, 9), (8, 9)] scores [0.5, 0.473, 0.452, 0.445, 0.441]

--- 2 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of elements'
    grounded on ch[8, 8, 8] at [0.404] (threshold 0.4)
  'human digestive system ke parts'
    rewritten -> 'human digestive system parts organs'
    grounded on ch[2, 2, 2] at [0.445] (threshold 0.4)

## SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --sweep 
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-74a03324be0b Creating 
Container guruji-api-run-74a03324be0b Created 
Corpus: 1677 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      99.0%   (target >80%)   n=103
MRR             0.964   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    95.1%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  92.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2614 ms

kind                     score    n
adjacent                  92%   12
direct                   100%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             95%   41
typo                     100%    1
vocab_free               100%   18

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   99.0%   0.964    80.5%     98.1%
  0.20      0.40   99.0%   0.964    95.1%     92.2%
  0.20      0.45   99.0%   0.964    97.6%     86.4%
  0.25      0.35   99.0%   0.964    80.5%     98.1%
  0.25      0.40   99.0%   0.964    95.1%     92.2%
  0.25      0.45   99.0%   0.964    97.6%     86.4%
  0.28      0.35   99.0%   0.964    80.5%     98.1%
  0.28      0.40   99.0%   0.964    95.1%     92.2%
  0.28      0.45   99.0%   0.964    97.6%     86.4%
  0.30      0.35   99.0%   0.964    80.5%     98.1%
  0.30      0.40   99.0%   0.964    95.1%     92.2%
  0.30      0.45   99.0%   0.964    97.6%     86.4%
  0.35      0.35   97.1%   0.945    80.5%     98.1%
  0.35      0.40   97.1%   0.945    95.1%     92.2%
  0.35      0.45   97.1%   0.945    97.6%     86.4%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

## NO-REWRITE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --no-rewrite
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-852682e14f5f Creating 
Container guruji-api-run-852682e14f5f Created 
Corpus: 1677 chunks. Eval set: 158 rows. Query rewrite: OFF (pre-fix baseline). Lexical rescue: ON

Recall@5      71.8%   (target >80%)   n=103
MRR             0.682   (target >0.70)
Gate accuracy   0.0%   (non-questions correctly skipped)   n=14
Refusal acc.    92.7%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  49.5%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   1014 ms

kind                     score    n
adjacent                  50%   12
direct                   100%   24
followup                  12%    8
hinglish                  95%   40
nonsearch/gate             0%   14
out_of_corpus             93%   41
typo                     100%    1
vocab_free                22%   18

---

# Class 10 - Compare

## VERBOSE & SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-418bfddc1c9f Creating 
Container guruji-api-run-418bfddc1c9f Created 
Corpus: 2409 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      99.0%   (target >80%)   n=103
MRR             0.961   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    92.7%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  94.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2302 ms

kind                     score    n
adjacent                  92%   12
direct                   100%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             93%   41
typo                     100%    1
vocab_free               100%   18

--- 1 retrieval misses (wrong chapter) ---
[adjacent] 'namak aur reth ko alag kaise karein'
    rewritten -> 'separation of salt and sand mixture by dissolving filtration and evaporation'
    wanted ch.8, got (class, ch) [(8, 9), (8, 9), (8, 9), (8, 9), (8, 9)] scores [0.485, 0.453, 0.442, 0.432, 0.428]

--- 3 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'human digestive system ke parts'
    rewritten -> 'human digestive system parts organs and functions'
    grounded on ch[2, 2, 2] at [0.479] (threshold 0.4)
  'Indian constitution kab bana tha'
    rewritten -> 'Indian Constitution making adoption and commencement dates'
    grounded on ch[11, 11, 11] at [0.412, 0.403] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   99.0%   0.961    90.2%     98.1%
  0.20      0.40   99.0%   0.961    92.7%     94.2%
  0.20      0.45   99.0%   0.961    97.6%     88.3%
  0.25      0.35   99.0%   0.961    90.2%     98.1%
  0.25      0.40   99.0%   0.961    92.7%     94.2%
  0.25      0.45   99.0%   0.961    97.6%     88.3%
  0.28      0.35   99.0%   0.961    90.2%     98.1%
  0.28      0.40   99.0%   0.961    92.7%     94.2%
  0.28      0.45   99.0%   0.961    97.6%     88.3%
  0.30      0.35   99.0%   0.961    90.2%     98.1%
  0.30      0.40   99.0%   0.961    92.7%     94.2%
  0.30      0.45   99.0%   0.961    97.6%     88.3%
  0.35      0.35   97.1%   0.942    90.2%     98.1%
  0.35      0.40   97.1%   0.942    92.7%     94.2%
  0.35      0.45   97.1%   0.942    97.6%     88.3%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

## NO-REWRITE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --no-rewrite    
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-6d3b971beebb Creating 
Container guruji-api-run-6d3b971beebb Created 
Corpus: 2409 chunks. Eval set: 158 rows. Query rewrite: OFF (pre-fix baseline). Lexical rescue: ON

Recall@5      71.8%   (target >80%)   n=103
MRR             0.682   (target >0.70)
Gate accuracy   0.0%   (non-questions correctly skipped)   n=14
Refusal acc.    92.7%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  49.5%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   605 ms

kind                     score    n
adjacent                  50%   12
direct                   100%   24
followup                  12%    8
hinglish                  95%   40
nonsearch/gate             0%   14
out_of_corpus             93%   41
typo                     100%    1
vocab_free                22%   18

---

# Class 6 - Compare

## VERBOSE & SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-7ab0d0e52d5e Creating 
Container guruji-api-run-7ab0d0e52d5e Created 
Corpus: 3009 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.1%   (target >80%)   n=103
MRR             0.950   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    85.4%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  94.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2613 ms

kind                     score    n
adjacent                  92%   12
direct                   100%   24
followup                 100%    8
hinglish                  98%   40
nonsearch/gate           100%   14
out_of_corpus             85%   41
typo                     100%    1
vocab_free               100%   18

--- 2 retrieval misses (wrong chapter) ---
[hinglish] 'mixture ko kaise separate karte hain'
    rewritten -> 'separation of mixtures methods filtration evaporation distillation sedimentation'
    wanted ch.8, got (class, ch) [(6, 9), (6, 9), (6, 9), (6, 9), (6, 9)] scores [0.678, 0.644, 0.632, 0.623, 0.618]
[adjacent] 'namak aur reth ko alag kaise karein'
    rewritten -> 'separation of salt and sand mixture solubility filtration evaporation'
    wanted ch.8, got (class, ch) [(6, 9), (6, 9), (6, 9), (6, 9), (6, 9)] scores [0.619, 0.581, 0.571, 0.569, 0.562]

--- 6 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'coal aur petroleum kaise bante hain'
    rewritten -> 'coal and petroleum formation fossil fuels sedimentary rocks'
    grounded on ch[11, 11, 11] at [0.477, 0.432, 0.407, 0.405] (threshold 0.4)
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'human digestive system ke parts'
    rewritten -> 'human digestive system parts organs and functions'
    grounded on ch[2, 2, 2] at [0.479] (threshold 0.4)
  'Harappa civilisation ke baare mein batao'
    rewritten -> 'Harappan Civilization Indus Valley Civilization urban planning culture economy'
    grounded on ch[6, 12, 6] at [0.472] (threshold 0.4)
  'sandhi viched kaise karte hain'
    rewritten -> 'Sandhi viched word separation rules Hindi grammar'
    grounded on ch[9, 9, 9] at [0.42] (threshold 0.4)
  'excretion ka matlab kya hai'
    rewritten -> 'excretion removal of metabolic waste from the body'
    grounded on ch[10, 2, 2] at [0.437] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   98.1%   0.950    78.0%     99.0%
  0.20      0.40   98.1%   0.950    85.4%     94.2%
  0.20      0.45   98.1%   0.950    92.7%     87.4%
  0.25      0.35   98.1%   0.950    78.0%     99.0%
  0.25      0.40   98.1%   0.950    85.4%     94.2%
  0.25      0.45   98.1%   0.950    92.7%     87.4%
  0.28      0.35   98.1%   0.950    78.0%     99.0%
  0.28      0.40   98.1%   0.950    85.4%     94.2%
  0.28      0.45   98.1%   0.950    92.7%     87.4%
  0.30      0.35   98.1%   0.950    78.0%     99.0%
  0.30      0.40   98.1%   0.950    85.4%     94.2%
  0.30      0.45   98.1%   0.950    92.7%     87.4%
  0.35      0.35   97.1%   0.941    78.0%     99.0%
  0.35      0.40   97.1%   0.941    85.4%     94.2%
  0.35      0.45   97.1%   0.941    92.7%     87.4%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

## NO-REWRITE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --no-rewrite    
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-5a3ec7edd5cf Creating 
Container guruji-api-run-5a3ec7edd5cf Created 
Corpus: 3009 chunks. Eval set: 158 rows. Query rewrite: OFF (pre-fix baseline). Lexical rescue: ON

Recall@5      70.9%   (target >80%)   n=103
MRR             0.667   (target >0.70)
Gate accuracy   0.0%   (non-questions correctly skipped)   n=14
Refusal acc.    82.9%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  49.5%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   747 ms

kind                     score    n
adjacent                  42%   12
direct                   100%   24
followup                  12%    8
hinglish                  90%   40
nonsearch/gate             0%   14
out_of_corpus             83%   41
typo                     100%    1
vocab_free                33%   18

---

# Class 7 - Compare

## VERBOSE & SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-5143f392ecd0 Creating 
Container guruji-api-run-5143f392ecd0 Created 
Corpus: 3511 chunks. Eval set: 158 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.1%   (target >80%)   n=103
MRR             0.938   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    82.9%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  95.1%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2045 ms

kind                     score    n
adjacent                  92%   12
direct                   100%   24
followup                 100%    8
hinglish                  98%   40
nonsearch/gate           100%   14
out_of_corpus             83%   41
typo                     100%    1
vocab_free               100%   18

--- 2 retrieval misses (wrong chapter) ---
[hinglish] 'mixture ko kaise separate karte hain'
    rewritten -> 'separation of mixtures methods filtration evaporation distillation'
    wanted ch.8, got (class, ch) [(6, 9), (6, 9), (6, 9), (6, 9), (6, 9)] scores [0.648, 0.624, 0.6, 0.598, 0.584]
[adjacent] 'namak aur reth ko alag kaise karein'
    rewritten -> 'separation of salt and sand mixture using solubility filtration and evaporation'
    wanted ch.8, got (class, ch) [(6, 9), (6, 9), (6, 9), (6, 9), (6, 9)] scores [0.594, 0.57, 0.543, 0.54, 0.532]

--- 7 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'coal aur petroleum kaise bante hain'
    rewritten -> 'coal and petroleum formation fossil fuels'
    grounded on ch[11, 11, 11] at [0.495, 0.443] (threshold 0.4)
  'photosynthesis ka process samjhao'
    rewritten -> 'photosynthesis process sunlight chlorophyll carbon dioxide water glucose oxygen'
    grounded on ch[10, 10, 10] at [0.593, 0.535, 0.567, 0.563, 0.561] (threshold 0.4)
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'human digestive system ke parts'
    rewritten -> 'human digestive system parts organs'
    grounded on ch[9, 9, 2] at [0.49, 0.457, 0.445, 0.436, 0.418] (threshold 0.4)
  'Harappa civilisation ke baare mein batao'
    rewritten -> 'Harappan Civilization Indus Valley Civilization urban planning culture society economy'
    grounded on ch[6, 6, 12] at [0.477] (threshold 0.4)
  'excretion ka matlab kya hai'
    rewritten -> 'excretion removal of metabolic wastes from the body'
    grounded on ch[10, 9, 2] at [0.432] (threshold 0.4)
  'alkali aur acid ka pH kya hota hai'
    rewritten -> 'acids and alkalis pH scale acidic and alkaline values'
    grounded on ch[2, 2, 2] at [0.439, 0.434, 0.421, 0.42, 0.414] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   98.1%   0.938    78.0%    100.0%
  0.20      0.40   98.1%   0.938    82.9%     95.1%
  0.20      0.45   98.1%   0.938    90.2%     87.4%
  0.25      0.35   98.1%   0.938    78.0%    100.0%
  0.25      0.40   98.1%   0.938    82.9%     95.1%
  0.25      0.45   98.1%   0.938    90.2%     87.4%
  0.28      0.35   98.1%   0.938    78.0%    100.0%
  0.28      0.40   98.1%   0.938    82.9%     95.1%
  0.28      0.45   98.1%   0.938    90.2%     87.4%
  0.30      0.35   98.1%   0.938    78.0%    100.0%
  0.30      0.40   98.1%   0.938    82.9%     95.1%
  0.30      0.45   98.1%   0.938    90.2%     87.4%
  0.35      0.35   97.1%   0.936    78.0%    100.0%
  0.35      0.40   97.1%   0.936    82.9%     95.1%
  0.35      0.45   97.1%   0.936    90.2%     87.4%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

## NO-REWRITE
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --no-rewrite    
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-7020cfdab5ec Creating 
Container guruji-api-run-7020cfdab5ec Created 
Corpus: 3511 chunks. Eval set: 158 rows. Query rewrite: OFF (pre-fix baseline). Lexical rescue: ON

Recall@5      70.9%   (target >80%)   n=103
MRR             0.632   (target >0.70)
Gate accuracy   0.0%   (non-questions correctly skipped)   n=14
Refusal acc.    80.5%   (out-of-corpus correctly ungrounded)   n=41
Confident rate  50.5%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   422 ms

kind                     score    n
adjacent                  42%   12
direct                    96%   24
followup                  25%    8
hinglish                  90%   40
nonsearch/gate             0%   14
out_of_corpus             80%   41
typo                     100%    1
vocab_free                33%   18

--- 

# After updating the EVAL

## VERBOSE & SWEEP
 docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-e9f5639a2219 Creating 
Container guruji-api-run-e9f5639a2219 Created 
Corpus: 3511 chunks. Eval set: 191 rows. Query rewrite: ON. Lexical rescue: ON

openai call failed, no retry requested: Request timed out.
query planner failed, falling back to raw message: 
Recall@5      96.5%   (target >80%)   n=141
MRR             0.926   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    94.4%   (out-of-corpus correctly ungrounded)   n=36
Confident rate  95.7%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2768 ms

kind                     score    n
adjacent                 100%   12
covered                  100%    5
cross_class               91%   33
direct                    96%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             94%   36
typo                     100%    1
vocab_free               100%   18

--- 5 retrieval misses (wrong chapter) ---
[direct] 'real and virtual image difference'
    rewritten -> 'real image and virtual image differences image formation'
    asked as Class 8, wanted ch.10, got (class, ch) [(7, 11), (7, 11), (7, 11), (7, 11), (7, 11)] scores [0.445, 0.429, 0.408, 0.399, 0.398]
[cross_class] 'electricity circuit kaise kaam karta hai'
    rewritten -> 'electric circuit working electric current voltage resistance components'
    asked as Class 8, wanted ch.4, got (class, ch) [(7, 3), (7, 3), (7, 3), (7, 3), (7, 3)] scores [0.56, 0.544, 0.538, 0.515, 0.508]
[cross_class] 'motion ko kaise describe karte hain'
    rewritten -> 'description of motion position reference point distance displacement'
    asked as Class 7, wanted ch.8, got (class, ch) [(6, 5), (6, 5), (6, 5), (6, 5), (6, 5)] scores [0.45, 0.426, 0.425, 0.419, 0.414]
[cross_class] 'matter particles se bana hai kya'
    rewritten -> 'matter particles atoms and molecules'
    asked as Class 9, wanted ch.9, got (class, ch) [(8, 7), (8, 7), (8, 7), (8, 8), (8, 7)] scores [0.568, 0.559, 0.548, 0.457, 0.557]
[cross_class] 'temperature kaise naapte hain'
    rewritten -> 'temperature measurement thermometer Celsius Fahrenheit Kelvin'
    asked as Class 7, wanted ch.7, got (class, ch) [(6, 7), (6, 7), (6, 7), (6, 7), (6, 7)] scores [0.547, 0.533, 0.543, 0.525, 0.575]

--- 2 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table 118 chemical elements'
    grounded on ch[8, 8, 8] at [0.481, 0.469, 0.464, 0.451, 0.441] (threshold 0.4)
  'Harappa civilisation ke baare mein batao'
    rewritten -> 'Harappan Civilization Indus Valley Civilization history'
    grounded on ch[6, 4, 6] at [0.455] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   96.5%   0.926    83.3%     98.6%
  0.20      0.40   96.5%   0.926    94.4%     95.7%
  0.20      0.45   96.5%   0.926    94.4%     87.2%
  0.25      0.35   96.5%   0.926    83.3%     98.6%
  0.25      0.40   96.5%   0.926    94.4%     95.7%
  0.25      0.45   96.5%   0.926    94.4%     87.2%
  0.28      0.35   96.5%   0.926    83.3%     98.6%
  0.28      0.40   96.5%   0.926    94.4%     95.7%
  0.28      0.45   96.5%   0.926    94.4%     87.2%
  0.30      0.35   96.5%   0.926    83.3%     98.6%
  0.30      0.40   96.5%   0.926    94.4%     95.7%
  0.30      0.45   96.5%   0.926    94.4%     87.2%
  0.35      0.35   95.0%   0.917    83.3%     98.6%
  0.35      0.40   95.0%   0.917    94.4%     95.7%
  0.35      0.45   95.0%   0.917    94.4%     87.2%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

---

# Class 5 - EVS

## VERBOSE & SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-ef19a7a2d5f0 Creating 
Container guruji-api-run-ef19a7a2d5f0 Created 
Corpus: 3762 chunks. Eval set: 191 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.6%   (target >80%)   n=141
MRR             0.963   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    91.7%   (out-of-corpus correctly ungrounded)   n=36
Confident rate  97.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   3756 ms

kind                     score    n
adjacent                 100%   12
covered                  100%    5
cross_class               91%   33
direct                    96%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             92%   36
typo                     100%    1
vocab_free               100%   18

--- 2 retrieval misses (wrong chapter) ---
[direct] 'real and virtual image difference'
    rewritten -> 'real image and virtual image differences image formation'
    asked as Class 8, wanted ch.10, got (class, ch) [(7, 11), (7, 11), (7, 11), (7, 11), (7, 11)] scores [0.445, 0.429, 0.408, 0.399, 0.398]
[cross_class] 'temperature kaise naapte hain'
    rewritten -> 'temperature measurement thermometer temperature scales'
    asked as Class 7, wanted ch.7, got (class, ch) [(6, 7), (6, 7), (6, 7), (6, 7), (6, 7)] scores [0.552, 0.539, 0.54, 0.529, 0.506]

--- 3 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'Indian constitution kab bana tha'
    rewritten -> 'Indian Constitution making adoption and enforcement dates'
    grounded on ch[11, 11, 11] at [0.402] (threshold 0.4)
  'Harappa civilisation ke baare mein batao'
    rewritten -> 'Harappan Civilization Indus Valley Civilization urban planning culture economy decline'
    grounded on ch[6, 12, 4] at [0.426] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   98.6%   0.963    83.3%    100.0%
  0.20      0.40   98.6%   0.963    91.7%     97.2%
  0.20      0.45   98.6%   0.963   100.0%     87.2%
  0.25      0.35   98.6%   0.963    83.3%    100.0%
  0.25      0.40   98.6%   0.963    91.7%     97.2%
  0.25      0.45   98.6%   0.963   100.0%     87.2%
  0.28      0.35   98.6%   0.963    83.3%    100.0%
  0.28      0.40   98.6%   0.963    91.7%     97.2%
  0.28      0.45   98.6%   0.963   100.0%     87.2%
  0.30      0.35   98.6%   0.963    83.3%    100.0%
  0.30      0.40   98.6%   0.963    91.7%     97.2%
  0.30      0.45   98.6%   0.963   100.0%     87.2%
  0.35      0.35   98.6%   0.963    83.3%    100.0%
  0.35      0.40   98.6%   0.963    91.7%     97.2%
  0.35      0.45   98.6%   0.963   100.0%     87.2%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

---

# Last Eval Setting

## VERBOSE & SWEEP
docker compose run --rm -v "$(pwd):/workspace" -w /workspace/backend \
  api python eval_retrieval.py --verbose --sweep
[+]  1/1t 1/11
 ✔ Container guruji-db-1 Running                                                                                                                        0.0s
Container guruji-db-1 Waiting 
Container guruji-db-1 Healthy 
Container guruji-api-run-429f26730ed7 Creating 
Container guruji-api-run-429f26730ed7 Created 
Corpus: 3762 chunks. Eval set: 191 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      99.3%   (target >80%)   n=141
MRR             0.972   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=14
Refusal acc.    88.9%   (out-of-corpus correctly ungrounded)   n=36
Confident rate  97.9%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   3704 ms

kind                     score    n
adjacent                 100%   12
covered                  100%    5
cross_class               97%   33
direct                   100%   24
followup                 100%    8
hinglish                 100%   40
nonsearch/gate           100%   14
out_of_corpus             89%   36
typo                     100%    1
vocab_free               100%   18

--- 1 retrieval misses (wrong chapter) ---
[cross_class] 'temperature kaise naapte hain'
    rewritten -> 'temperature measurement thermometer temperature scales'
    asked as Class 7, wanted ch.7, got (class, ch) [(6, 7), (6, 7), (6, 7), (6, 7), (6, 7)] scores [0.552, 0.539, 0.54, 0.529, 0.506]

--- 4 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'periodic table mein kitne elements hain'
    rewritten -> 'periodic table number of chemical elements'
    grounded on ch[8, 8, 8] at [0.424] (threshold 0.4)
  'Indian constitution kab bana tha'
    rewritten -> 'Indian Constitution making adoption and commencement dates'
    grounded on ch[11, 11, 11] at [0.412, 0.403] (threshold 0.4)
  'Harappa civilisation ke baare mein batao'
    rewritten -> 'Harappan Civilization Indus Valley Civilization'
    grounded on ch[6, 2, 6] at [0.445] (threshold 0.4)
  'sandhi viched kaise karte hain'
    rewritten -> 'Sandhi viched Hindi grammar word separation rules'
    grounded on ch[9, 9, 9] at [0.424, 0.404] (threshold 0.4)

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   99.3%   0.972    86.1%    100.0%
  0.20      0.40   99.3%   0.972    88.9%     97.9%
  0.20      0.45   99.3%   0.972   100.0%     90.1%
  0.25      0.35   99.3%   0.972    86.1%    100.0%
  0.25      0.40   99.3%   0.972    88.9%     97.9%
  0.25      0.45   99.3%   0.972   100.0%     90.1%
  0.28      0.35   99.3%   0.972    86.1%    100.0%
  0.28      0.40   99.3%   0.972    88.9%     97.9%
  0.28      0.45   99.3%   0.972   100.0%     90.1%
  0.30      0.35   99.3%   0.972    86.1%    100.0%
  0.30      0.40   99.3%   0.972    88.9%     97.9%
  0.30      0.45   99.3%   0.972   100.0%     90.1%
  0.35      0.35   99.3%   0.972    86.1%    100.0%
  0.35      0.40   99.3%   0.972    88.9%     97.9%
  0.35      0.45   99.3%   0.972   100.0%     90.1%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

---
---

# Mathematics — Class 8 baseline

Corpus: Class 8 Mathematics only, 14 chapters, RAG_THRESHOLD=0.40.
Eval harness includes the subject-scoping fix (Science and Mathematics no
longer collide on shared (grade, chapter_no) pairs).

## VERBOSE

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      86.5%   (target >80%)   n=52
MRR             0.804   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    66.7%   (out-of-corpus correctly ungrounded)   n=6
Confident rate  96.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   3830 ms

kind                     score    n
adjacent                  67%    3
direct                   100%   14
followup                 100%    2
hinglish                  86%   22
nonsearch/gate           100%    2
out_of_corpus             67%    6
typo                     100%    2
vocab_free                67%    9

--- 7 retrieval misses (wrong chapter) ---
[adjacent] 'how many times can paper actually be folded in half'
    rewritten -> ''
    asked as Class 8, wanted ch.2, got (class, ch) [] scores []
[hinglish] 'rational number kya hota hai?'
    rewritten -> 'rational numbers definition integers numerator denominator'
    asked as Class 8, wanted ch.3, got (class, ch) [(8, 5), (8, 5), (8, 5), (8, 13), (8, 5)] scores [0.42, 0.411, 0.41, 0.402, 0.398]
[vocab_free] 'number line par fraction kaise dikhate hain'
    rewritten -> 'fractions representation on a number line'
    asked as Class 8, wanted ch.3, got (class, ch) [(8, 8), (8, 8), (8, 8), (8, 8), (8, 8)] scores [0.492, 0.47, 0.466, 0.466, 0.465]
[hinglish] 'factorise kaise karte hain algebra mein?'
    rewritten -> 'algebraic factorisation methods factors polynomials'
    asked as Class 8, wanted ch.6, got (class, ch) [(8, 1), (8, 1), (8, 1), (8, 13), (8, 1)] scores [0.451, 0.434, 0.43, 0.429, 0.423]
[vocab_free] 'do photos same size mein enlarge kaise karte hain proportionally'
    rewritten -> ''
    asked as Class 8, wanted ch.7, got (class, ch) [] scores []
[vocab_free] 'area barabar rakhte hue shape ko alag tareeke se todna'
    rewritten -> 'area conservation decomposing shapes into different geometric figures'
    asked as Class 8, wanted ch.14, got (class, ch) [(8, 11), (8, 11), (8, 11), (8, 11), (8, 11)] scores [0.516, 0.511, 0.51, 0.492, 0.49]
[hinglish] 'cube ka volume kaise nikalte hain?'
    rewritten -> 'cube volume formula side length'
    asked as Class 8, wanted ch.1, got (class, ch) [(8, 14), (8, 11), (8, 9), (8, 14), (8, 14)] scores [0.44, 0.429, 0.427, 0.426, 0.425]

--- 2 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'matrix multiplication kaise karte hain'
    rewritten -> 'matrix multiplication method rows columns'
    grounded on ch[6, 6, 6] at [0.47, 0.457, 0.437, 0.425, 0.418] (threshold 0.4)
  'quadratic equation solve kaise karte hain'
    rewritten -> 'quadratic equation solving methods roots formula factorization'
    grounded on ch[1, 1, 1] at [0.404] (threshold 0.4)

## SWEEP

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      86.5%   (target >80%)   n=52
MRR             0.824   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    66.7%   (out-of-corpus correctly ungrounded)   n=6
Confident rate  96.2%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2181 ms

kind                     score    n
adjacent                  67%    3
direct                   100%   14
followup                 100%    2
hinglish                  82%   22
nonsearch/gate           100%    2
out_of_corpus             67%    6
typo                     100%    2
vocab_free                78%    9

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   86.5%   0.824    50.0%     96.2%
  0.20      0.40   86.5%   0.824    66.7%     96.2%
  0.20      0.45   86.5%   0.824    83.3%     86.5%
  0.25      0.35   86.5%   0.824    50.0%     96.2%
  0.25      0.40   86.5%   0.824    66.7%     96.2%
  0.25      0.45   86.5%   0.824    83.3%     86.5%
  0.28      0.35   86.5%   0.824    50.0%     96.2%
  0.28      0.40   86.5%   0.824    66.7%     96.2%
  0.28      0.45   86.5%   0.824    83.3%     86.5%
  0.30      0.35   86.5%   0.824    50.0%     96.2%
  0.30      0.40   86.5%   0.824    66.7%     96.2%
  0.30      0.45   86.5%   0.824    83.3%     86.5%
  0.35      0.35   86.5%   0.824    50.0%     96.2%
  0.35      0.40   86.5%   0.824    66.7%     96.2%
  0.35      0.45   86.5%   0.824    83.3%     86.5%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

---

# Mathematics — Class 8 Rerun

Corpus: Class 8 Mathematics only, 14 chapters, RAG_THRESHOLD=0.40.
Eval harness includes the subject-scoping fix (Science and Mathematics no
longer collide on shared (grade, chapter_no) pairs).

## VERBOSE

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      94.2%   (target >80%)   n=52
MRR             0.870   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    50.0%   (out-of-corpus correctly ungrounded)   n=6
Confident rate  100.0%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   4324 ms

kind                     score    n
adjacent                 100%    3
direct                   100%   14
followup                 100%    2
hinglish                  91%   22
nonsearch/gate           100%    2
out_of_corpus             50%    6
typo                     100%    2
vocab_free                89%    9

--- 3 retrieval misses (wrong chapter) ---
[hinglish] 'rational number kya hota hai?'
    rewritten -> 'rational numbers definition fractions integers denominator nonzero'
    asked as Class 8, wanted ch.3, got (class, ch) [(8, 5), (8, 5), (8, 5), (8, 5), (8, 5)] scores [0.426, 0.426, 0.423, 0.42, 0.417]
[hinglish] 'factorise kaise karte hain algebra mein?'
    rewritten -> 'algebraic factorisation methods factors and factorisation'
    asked as Class 8, wanted ch.6, got (class, ch) [(8, 1), (8, 1), (8, 1), (8, 1), (8, 13)] scores [0.482, 0.466, 0.462, 0.454, 0.44]
[vocab_free] 'area barabar rakhte hue shape ko alag tareeke se todna'
    rewritten -> 'area-preserving decomposition and rearrangement of geometric shapes'
    asked as Class 8, wanted ch.14, got (class, ch) [(8, 11), (8, 11), (8, 11), (8, 11), (8, 11)] scores [0.468, 0.448, 0.443, 0.438, 0.435]

--- 3 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'matrix multiplication kaise karte hain'
    rewritten -> 'matrix multiplication method rows columns dot product'
    grounded on ch[6, 6, 6] at [0.445, 0.433, 0.431, 0.424, 0.422] (threshold 0.4)
  'trigonometry ke ratios kya hain — sin cos tan'
    rewritten -> 'trigonometric ratios sine cosine tangent sin cos tan right-angled triangle'
    grounded on ch[9, 4, 9] at [0.414] (threshold 0.4)
  'quadratic equation solve kaise karte hain'
    rewritten -> 'quadratic equation solving methods factorization quadratic formula completing the square'
    grounded on ch[1, 1, 1] at [0.424, 0.423, 0.422, 0.415, 0.414] (threshold 0.4)

## SWEEP

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      94.2%   (target >80%)   n=52
MRR             0.873   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    50.0%   (out-of-corpus correctly ungrounded)   n=6
Confident rate  100.0%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2462 ms

kind                     score    n
adjacent                 100%    3
direct                   100%   14
followup                 100%    2
hinglish                  91%   22
nonsearch/gate           100%    2
out_of_corpus             50%    6
typo                     100%    2
vocab_free                89%    9

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   94.2%   0.873    50.0%    100.0%
  0.20      0.40   94.2%   0.873    50.0%    100.0%
  0.20      0.45   94.2%   0.873    83.3%     92.3%
  0.25      0.35   94.2%   0.873    50.0%    100.0%
  0.25      0.40   94.2%   0.873    50.0%    100.0%
  0.25      0.45   94.2%   0.873    83.3%     92.3%
  0.28      0.35   94.2%   0.873    50.0%    100.0%
  0.28      0.40   94.2%   0.873    50.0%    100.0%
  0.28      0.45   94.2%   0.873    83.3%     92.3%
  0.30      0.35   94.2%   0.873    50.0%    100.0%
  0.30      0.40   94.2%   0.873    50.0%    100.0%
  0.30      0.45   94.2%   0.873    83.3%     92.3%
  0.35      0.35   94.2%   0.873    50.0%    100.0%
  0.35      0.40   94.2%   0.873    50.0%    100.0%
  0.35      0.45   94.2%   0.873    83.3%     92.3%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.

---

# Mathematics — Class 8 Rerun

Corpus: Class 8 Mathematics only, 14 chapters, RAG_THRESHOLD=0.40.
Eval harness includes the subject-scoping fix (Science and Mathematics no
longer collide on shared (grade, chapter_no) pairs).

## VERBOSE

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.0%   (target >80%)   n=50
MRR             0.965   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    37.5%   (out-of-corpus correctly ungrounded)   n=8
Confident rate  100.0%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   2982 ms

kind                     score    n
adjacent                 100%    3
direct                   100%   13
followup                 100%    2
hinglish                 100%   21
nonsearch/gate           100%    2
out_of_corpus             38%    8
typo                     100%    2
vocab_free                89%    9

--- 1 retrieval misses (wrong chapter) ---
[vocab_free] 'area barabar rakhte hue shape ko alag tareeke se todna'
    rewritten -> 'area-preserving rearrangement and decomposition of geometric shapes'
    asked as Class 8, wanted ch.14, got (class, ch) [(8, 11), (8, 11), (8, 11), (8, 11), (8, 11)] scores [0.473, 0.45, 0.443, 0.443, 0.442]

--- 5 FALSE GROUNDINGS (out-of-corpus answered confidently) ---
    The worst failure this product has: textbook authority asserted for
    content that is not in the textbook, to a reader who cannot check.
  'rational number kya hota hai?'
    rewritten -> 'rational numbers fractions integers terminating and recurring decimals'
    grounded on ch[9, 8, 5] at [0.468, 0.453, 0.446, 0.437, 0.434] (threshold 0.4)
  'Is every whole number a rational number?'
    rewritten -> 'rational numbers whole numbers integers fractions'
    grounded on ch[5, 5, 8] at [0.437, 0.433, 0.428, 0.427, 0.425] (threshold 0.4)
  'matrix multiplication kaise karte hain'
    rewritten -> 'matrix multiplication row by column method'
    grounded on ch[6, 6, 6] at [0.453, 0.453, 0.44, 0.413, 0.41] (threshold 0.4)
  'trigonometry ke ratios kya hain — sin cos tan'
    rewritten -> 'trigonometric ratios sine cosine tangent in right-angled triangles'
    grounded on ch[9, 4, 9] at [0.406] (threshold 0.4)
  'quadratic equation solve kaise karte hain'
    rewritten -> 'quadratic equations solving methods factorisation completing the square quadratic formula'
    grounded on ch[1, 1, 1] at [0.431, 0.421, 0.416, 0.415, 0.409] (threshold 0.4)

## SWEEP

Corpus: 4589 chunks. Eval set: 60 rows. Query rewrite: ON. Lexical rescue: ON

Recall@5      98.0%   (target >80%)   n=50
MRR             0.923   (target >0.70)
Gate accuracy   100.0%   (non-questions correctly skipped)   n=2
Refusal acc.    37.5%   (out-of-corpus correctly ungrounded)   n=8
Confident rate  98.0%   (in-corpus questions answered confidently, not hedged)
Retrieval p50   4858 ms

kind                     score    n
adjacent                 100%    3
direct                   100%   13
followup                 100%    2
hinglish                 100%   21
nonsearch/gate           100%    2
out_of_corpus             38%    8
typo                     100%    2
vocab_free                89%    9

--- threshold sweep (same retrieval, re-scored) ---
  weak  grounded   recall     mrr   refusal  confident
  0.20      0.35   98.0%   0.923    25.0%    100.0%
  0.20      0.40   98.0%   0.923    37.5%     98.0%
  0.20      0.45   98.0%   0.923    62.5%     92.0%
  0.25      0.35   98.0%   0.923    25.0%    100.0%
  0.25      0.40   98.0%   0.923    37.5%     98.0%
  0.25      0.45   98.0%   0.923    62.5%     92.0%
  0.28      0.35   98.0%   0.923    25.0%    100.0%
  0.28      0.40   98.0%   0.923    37.5%     98.0%
  0.28      0.45   98.0%   0.923    62.5%     92.0%
  0.30      0.35   98.0%   0.923    25.0%    100.0%
  0.30      0.40   98.0%   0.923    37.5%     98.0%
  0.30      0.45   98.0%   0.923    62.5%     92.0%
  0.35      0.35   98.0%   0.923    25.0%    100.0%
  0.35      0.40   98.0%   0.923    37.5%     98.0%
  0.35      0.45   98.0%   0.923    62.5%     92.0%

Read this as a TRADE, not an optimum. `recall` cannot move with
`grounded` (it depends only on `weak`), so a rising refusal column with
flat recall is NOT a free win. `confident` is the price: it is the share
of real in-corpus questions still answered confidently rather than hedged.
Every point of refusal accuracy above ~75% is bought with confident-rate.
