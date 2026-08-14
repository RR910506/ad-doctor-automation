def alert(st,message,kind='info'):
 getattr(st,kind if kind in {'info','warning','error','success'} else 'info')(message)
