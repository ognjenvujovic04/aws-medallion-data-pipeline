# Bitcoin Tweets — bronze staging



Source for this project: [kaushiksuresh147/bitcoin-tweets](https://www.kaggle.com/datasets/kaushiksuresh147/bitcoin-tweets) on Kaggle.



Place the extracted file here (default name: `bitcoin_tweets.csv`). Upload to the Data Lake with `aws s3 cp`.



**S3 path:** `bronze/twitter/dataset=bitcoin-tweets/bitcoin_tweets.csv`



## Download



```powershell

# from medallion-data-platform/

kaggle datasets download -d kaushiksuresh147/bitcoin-tweets -p data/bitcoin-tweets --unzip

```



## Git



`.gitignore` excludes `*.csv` and `*.zip` in this folder. `README.md` and `manifest.json` stay in git.



## Upload to bronze



```powershell


aws s3 cp "data/bitcoin-tweets/bitcoin_tweets.csv" "s3://<bucket>/bronze/twitter/dataset=bitcoin-tweets/bitcoin_tweets.csv"

```

## Verify in S3



```powershell

aws s3 ls s3://<bucket>/bronze/twitter/dataset=bitcoin-tweets/ --recursive --human-readable

```



Expect `bitcoin_tweets.csv` at ~2 GB.

