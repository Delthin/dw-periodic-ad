#!/usr/bin/env python3
"""Training-only robust seasonal autoregression and causal rolling innovations.

Huber IRLS is a fitting ingredient (Huber 1973); the lag set, ridge penalty,
lag-input clipping and combination with MAD/EWMA are project design choices,
not a reproduction of Tsay's intervention tests or a claim of a new AR theory.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def fit_predictor(values,train_end,period,spec):
    x=np.asarray(values,dtype=float)[:train_end]
    lags=sorted({1,2,3,6,12,int(period),2*int(period)})
    first=max(lags)
    if len(x)<=first+len(lags):raise ValueError('Insufficient fit prefix for autoregression')
    center=float(np.median(x));scale=max(float(1.4826*np.median(np.abs(x-center))),
        float(np.subtract(*np.percentile(x,[75,25]))/1.349),float(np.std(x)*.1),1e-9)
    normalized=(x-center)/scale
    targets=np.arange(first,len(x));targets=targets[np.linspace(0,len(targets)-1,min(spec['train_limit'],len(targets)),dtype=int)]
    X=np.column_stack([np.ones(len(targets)),*[np.clip(normalized[targets-lag],-spec['lag_clip'],spec['lag_clip']) for lag in lags]])
    y=normalized[targets];penalty=np.eye(X.shape[1])*spec['ridge']*len(y);penalty[0,0]=0.
    weights=np.ones(len(y));coef=np.zeros(X.shape[1])
    for _ in range(spec['irls_iterations']):
        coef=np.linalg.solve(X.T@(weights[:,None]*X)+penalty,X.T@(weights*y))
        residual=y-X@coef;middle=float(np.median(residual));s=max(float(1.4826*np.median(np.abs(residual-middle))),1e-6)
        weights=np.minimum(1.,spec['huber_delta']*s/np.maximum(np.abs(residual),1e-12))
    return {'lags':lags,'center':center,'scale':scale,'lag_clip':spec['lag_clip'],
            'intercept':float(coef[0]),'coefficients':coef[1:].tolist(),'fit_points':len(y),
            'ridge':spec['ridge'],'huber_delta':spec['huber_delta'],'irls_iterations':spec['irls_iterations']}


def predictions(values,model):
    x=np.asarray(values,dtype=float);n=len(x);start=max(model['lags']);out=np.full(n,np.nan)
    t=np.arange(start,n);z=(x-model['center'])/model['scale']
    p=np.full(len(t),model['intercept'],dtype=float)
    # Keep an explicit, fixed summation order for the production Java port.
    for lag,coef in zip(model['lags'],model['coefficients']):
        p=p+coef*np.clip(z[t-lag],-model['lag_clip'],model['lag_clip'])
    out[start:]=model['center']+model['scale']*p
    return out


def prediction_trace(frame,model,window):
    x=frame.value.to_numpy(dtype=float);ts=frame.timestamp_ms.to_numpy(dtype=np.int64)
    baseline=predictions(x,model);r=x-baseline;start=max(model['lags']);first=start+window
    if first>=len(x):raise ValueError('No prediction residual warmup')
    initial=r[start:first];m=float(np.median(initial));epsilon=max(1e-9,1e-3*1.4826*float(np.median(np.abs(initial-m))))
    histories=np.lib.stride_tricks.sliding_window_view(r[start:],window)[:-1]
    centers=np.empty(len(histories));scales=np.empty(len(histories))
    for offset in range(0,len(histories),2048):
        block=histories[offset:offset+2048];c=np.median(block,axis=1)
        centers[offset:offset+len(block)]=c
        scales[offset:offset+len(block)]=np.maximum(1.4826*np.median(np.abs(block-c[:,None]),axis=1),epsilon)
    return pd.DataFrame({'timestamp_ms':ts[first:],'value':x[first:],'baseline':baseline[first:],
                         'residual':r[first:],'residual_center':centers,'scale':scales})
