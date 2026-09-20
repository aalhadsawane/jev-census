question     type    accuracy  n   ECE    threshold  coverage  verdict
churn_risk   noul    0.97      30  —      —          —         PASS   
department   choice  0.87      30  0.110  —          —         FAIL   
frustration  score   0.80      30  0.130  —          —         FAIL   
is_urgent    noul    0.90      30  —      —          —         PASS   

### churn_risk
- n = 30, unclear = 0
- weighted accuracy 0.967 (unweighted 0.967), 95% Wilson CI [0.833, 0.994], n_eff = 30.0
- noul probability calibration (ece_probability): 0.058

### department
- n = 30, unclear = 0
- weighted accuracy 0.867 (unweighted 0.867), 95% Wilson CI [0.703, 0.947], n_eff = 30.0

### frustration
- n = 30, unclear = 0
- weighted accuracy 0.800 (unweighted 0.800), 95% Wilson CI [0.627, 0.905], n_eff = 30.0
- adjacent agreement (|modal level - gold| <= 1): 1.000

### is_urgent
- n = 30, unclear = 0
- weighted accuracy 0.900 (unweighted 0.900), 95% Wilson CI [0.744, 0.965], n_eff = 30.0
- noul probability calibration (ece_probability): 0.167
