import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$")
logger = logging.getLogger("vpc_sentinel")

app = FastAPI(
    title="VPC Sentinel API",
    description="Read-only AWS VPC inventory for the VPC Sentinel dashboard.",
    version="1.0.0",
)


@lru_cache(maxsize=16)
def aws_client(service: str, region: str) -> Any:
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client(service, region_name=region)


def validate_region(region: str) -> str:
    if not REGION_PATTERN.fullmatch(region):
        raise HTTPException(status_code=422, detail="Provide a valid AWS region, such as us-east-1.")
    return region


def aws_call(operation: str, call: Any) -> Any:
    try:
        return call()
    except NoCredentialsError as exc:
        logger.warning("AWS credentials are not configured for %s", operation)
        raise HTTPException(
            status_code=503,
            detail="AWS credentials not found. Configure an AWS profile or the standard AWS credential environment variables.",
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "AWSRequestError")
        logger.warning("AWS rejected %s with %s", operation, code)
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            raise HTTPException(
                status_code=403,
                detail="AWS denied this read request. Check the IAM permissions for the configured identity.",
            ) from exc
        if code in {"InvalidVpcID.NotFound", "InvalidVpcID.Malformed"}:
            raise HTTPException(
                status_code=404,
                detail="The requested VPC was not found in this region.",
            ) from exc
        raise HTTPException(status_code=502, detail="AWS could not complete the read request.") from exc
    except BotoCoreError as exc:
        logger.warning("AWS connection or configuration error during %s: %s", operation, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not connect to AWS. Check your region, credentials, and network connection.",
        ) from exc


def pages(client: Any, operation: str, result_key: str, **kwargs: Any) -> list[dict[str, Any]]:
    paginator = client.get_paginator(operation)
    result: list[dict[str, Any]] = []
    for page in paginator.paginate(**kwargs):
        result.extend(page.get(result_key, []))
    return result


def vpc_name(tags: list[dict[str, str]] | None) -> str | None:
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def compact_vpc(vpc: dict[str, Any]) -> dict[str, Any]:
    return {
        "vpc_id": vpc["VpcId"],
        "cidr_block": vpc.get("CidrBlock", ""),
        "state": vpc.get("State", "unknown"),
        "name": vpc_name(vpc.get("Tags")),
        "is_default": vpc.get("IsDefault", False),
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/vpcs")
def list_vpcs(
    region: str = Query(default="us-east-1", min_length=5, max_length=32),
) -> dict[str, Any]:
    validate_region(region)
    ec2 = aws_call("create EC2 client", lambda: aws_client("ec2", region))
    vpcs = aws_call("describe VPCs", lambda: pages(ec2, "describe_vpcs", "Vpcs"))
    return {"region": region, "vpcs": [compact_vpc(vpc) for vpc in vpcs]}


@app.get("/api/vpcs/{vpc_id}")
def get_vpc(
    vpc_id: str,
    region: str = Query(default="us-east-1", min_length=5, max_length=32),
) -> dict[str, Any]:
    validate_region(region)
    if not re.fullmatch(r"vpc-[a-fA-F0-9]+", vpc_id):
        raise HTTPException(status_code=422, detail="Provide a valid VPC ID.")

    ec2 = aws_call("create EC2 client", lambda: aws_client("ec2", region))
    vpcs = aws_call(
        "describe VPC",
        lambda: pages(ec2, "describe_vpcs", "Vpcs", VpcIds=[vpc_id]),
    )
    if not vpcs:
        raise HTTPException(status_code=404, detail="The requested VPC was not found in this region.")

    vpc_filter = [{"Name": "vpc-id", "Values": [vpc_id]}]
    subnets = aws_call(
        "describe subnets",
        lambda: pages(ec2, "describe_subnets", "Subnets", Filters=vpc_filter),
    )
    route_tables = aws_call(
        "describe route tables",
        lambda: pages(ec2, "describe_route_tables", "RouteTables", Filters=vpc_filter),
    )
    security_groups = aws_call(
        "describe security groups",
        lambda: pages(ec2, "describe_security_groups", "SecurityGroups", Filters=vpc_filter),
    )
    network_acls = aws_call(
        "describe network ACLs",
        lambda: pages(ec2, "describe_network_acls", "NetworkAcls", Filters=vpc_filter),
    )
    internet_gateways = aws_call(
        "describe internet gateways",
        lambda: pages(ec2, "describe_internet_gateways", "InternetGateways", Filters=vpc_filter),
    )
    nat_gateways = aws_call(
        "describe NAT gateways",
        lambda: pages(ec2, "describe_nat_gateways", "NatGateways", Filter=vpc_filter),
    )
    reservations = aws_call(
        "describe EC2 instances",
        lambda: pages(ec2, "describe_instances", "Reservations", Filters=vpc_filter),
    )
    elbv2 = aws_call("create ELBv2 client", lambda: aws_client("elbv2", region))
    load_balancers = aws_call(
        "describe load balancers",
        lambda: pages(elbv2, "describe_load_balancers", "LoadBalancers"),
    )

    compact_subnets = [
        {
            "subnet_id": item["SubnetId"],
            "cidr_block": item.get("CidrBlock", ""),
            "availability_zone": item.get("AvailabilityZone", ""),
            "map_public_ip": item.get("MapPublicIpOnLaunch", False),
            "state": item.get("State", "unknown"),
            "name": vpc_name(item.get("Tags")),
        }
        for item in subnets
    ]
    compact_routes = [
        {
            "route_table_id": item["RouteTableId"],
            "routes": [
                {
                    "destination": route.get("DestinationCidrBlock") or route.get("DestinationIpv6CidrBlock"),
                    "target": next(
                        (
                            route.get(key)
                            for key in (
                                "GatewayId",
                                "NatGatewayId",
                                "TransitGatewayId",
                                "NetworkInterfaceId",
                                "VpcPeeringConnectionId",
                                "InstanceId",
                            )
                            if route.get(key)
                        ),
                        "local",
                    ),
                    "state": route.get("State", "unknown"),
                }
                for route in item.get("Routes", [])
            ],
            "associations": [
                association.get("SubnetId", "main")
                for association in item.get("Associations", [])
            ],
        }
        for item in route_tables
    ]
    compact_security_groups = [
        {
            "group_id": item["GroupId"],
            "group_name": item.get("GroupName", ""),
            "description": item.get("Description", ""),
            "ingress_rule_count": len(item.get("IpPermissions", [])),
            "egress_rule_count": len(item.get("IpPermissionsEgress", [])),
            "ingress": item.get("IpPermissions", []),
            "egress": item.get("IpPermissionsEgress", []),
        }
        for item in security_groups
    ]
    compact_acls = [
        {
            "network_acl_id": item["NetworkAclId"],
            "is_default": item.get("IsDefault", False),
            "entries": [
                {
                    "rule_number": entry.get("RuleNumber"),
                    "protocol": entry.get("Protocol"),
                    "action": entry.get("RuleAction"),
                    "egress": entry.get("Egress", False),
                    "cidr": entry.get("CidrBlock") or entry.get("Ipv6CidrBlock"),
                }
                for entry in item.get("Entries", [])
            ],
        }
        for item in network_acls
    ]
    compact_instances = [
        {
            "instance_id": instance["InstanceId"],
            "name": vpc_name(instance.get("Tags")),
            "state": instance.get("State", {}).get("Name", "unknown"),
            "instance_type": instance.get("InstanceType", ""),
            "availability_zone": instance.get("Placement", {}).get("AvailabilityZone", ""),
            "subnet_id": instance.get("SubnetId", ""),
            "private_ip": instance.get("PrivateIpAddress"),
            "security_groups": [
                group.get("GroupId", "")
                for group in instance.get("SecurityGroups", [])
            ],
        }
        for reservation in reservations
        for instance in reservation.get("Instances", [])
    ]

    return {
        "region": region,
        "vpc": compact_vpc(vpcs[0]),
        "subnets": compact_subnets,
        "route_tables": compact_routes,
        "security_groups": compact_security_groups,
        "network_acls": compact_acls,
        "internet_gateways": [
            {
                "internet_gateway_id": item["InternetGatewayId"],
                "state": next(
                    (
                        attachment.get("State", "unknown")
                        for attachment in item.get("Attachments", [])
                        if attachment.get("VpcId") == vpc_id
                    ),
                    "detached",
                ),
            }
            for item in internet_gateways
        ],
        "nat_gateways": [
            {
                "nat_gateway_id": item["NatGatewayId"],
                "subnet_id": item.get("SubnetId", ""),
                "state": item.get("State", "unknown"),
            }
            for item in nat_gateways
        ],
        "instances": compact_instances,
        "load_balancers": [
            {
                "name": item["LoadBalancerName"],
                "type": item.get("Type", ""),
                "state": item.get("State", {}).get("Code", "unknown"),
                "scheme": item.get("Scheme", ""),
                "dns_name": item.get("DNSName", ""),
            }
            for item in load_balancers
            if item.get("VpcId") == vpc_id
        ],
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/styles.css", include_in_schema=False)
def styles() -> FileResponse:
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def script() -> FileResponse:
    return FileResponse(BASE_DIR / "app.js", media_type="text/javascript")
