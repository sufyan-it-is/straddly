import requests
base='https://straddly.pro/api/v2'
headers={'X-AUTH':'1|GQq5Q1JESHaawnDJ5kvW0lFevUgU4o2abzcH27y2b3b38466','Content-Type':'application/json'}
tests=[
    {'name':'ticker_with_token_and_user', 'payload':{'symbol':'RELIANCE','token':2885,'security_id':2885,'exchange':'NSE','quantity':1,'price':1430.0,'product_type':'MIS','transaction_type':'BUY','user_id':'x8gg0og8440wkgc8ow0ococs'}},
    {'name':'ticker_no_userid','payload':{'symbol':'RELIANCE','security_id':2885,'exchange':'NSE','quantity':1,'price':1430.0,'product_type':'MIS','transaction_type':'BUY'}},
    {'name':'display_name_no_token','payload':{'symbol':'Reliance Industries Limited','exchange':'NSE','quantity':1,'price':1430.0,'product_type':'MIS','transaction_type':'BUY','user_id':'x8gg0og8440wkgc8ow0ococs'}},
    {'name':'ticker_price0','payload':{'symbol':'RELIANCE','security_id':2885,'exchange':'NSE','quantity':1,'price':0.0,'product_type':'MIS','transaction_type':'BUY','user_id':'x8gg0og8440wkgc8ow0ococs'}},
    {'name':'only_underlying','payload':{'symbol':'RELIANCE','exchange':'NSE','quantity':1,'product_type':'MIS','transaction_type':'BUY','user_id':'x8gg0og8440wkgc8ow0ococs'}},
]
for t in tests:
    try:
        r=requests.post(base+'/margin/calculate',json=t['payload'],headers=headers,timeout=10)
        print(t['name'], r.status_code, r.text[:200])
    except Exception as e:
        print(t['name'],'ERROR',e)
