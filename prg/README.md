# VPC Sentinel

VPC Sentinel serves the secure VPC dashboard and provides a **read-only** AWS inventory API. It does not create, update, or delete AWS resources.

## Run locally

Requires Python 3.10 or newer and AWS credentials configured on the same machine.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:AWS_PROFILE = "your-read-only-profile"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, choose **Load AWS data**, enter a region, then select a VPC to inspect its subnets, routes, security groups, network ACLs, gateways, instances, and load balancers. The API documentation is at `http://127.0.0.1:8000/docs`.

The region defaults to `us-east-1`. Set `AWS_PROFILE` to the name in your local AWS config, or use the standard AWS credential chain. Credentials stay on the server and are never sent to the browser.

## Least-privilege IAM permissions

For inventory access, grant only the read/list actions used by the API. Scope resource-level permissions where AWS supports it; many EC2 describe actions require `"Resource": "*"`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeNetworkAcls",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeNatGateways",
        "ec2:DescribeInstances",
        "elasticloadbalancing:DescribeLoadBalancers"
      ],
      "Resource": "*"
    }
  ]
}
```

## Security notes

- The development server binds to loopback (`127.0.0.1`) and has no AWS credentials or secrets in frontend code.
- Do not expose this development server directly to the internet. Before deploying for multiple users, add authentication, authorization, TLS, request limits, and appropriately isolated per-user AWS roles.
- VPC names, private addresses, routes, and firewall rule summaries can be sensitive infrastructure information. Restrict access and avoid sharing the inventory publicly.
- The dashboard is an inventory view and does not provision the architecture shown in its design preview.
