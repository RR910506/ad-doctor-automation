def paginate(items,page,page_size=10):
 total=len(items);pages=max(1,(total+page_size-1)//page_size);page=max(1,min(page,pages));return items[(page-1)*page_size:page*page_size],{'page':page,'pages':pages,'total':total,'page_size':page_size}
def pagination_controls(st,key,meta):
 cols=st.columns([1,2,1]);cols[0].caption(f"{meta['total']} results");page=cols[1].number_input('Page',1,meta['pages'],meta['page'],key=f'{key}_page');cols[2].caption(f"of {meta['pages']}");return int(page)
