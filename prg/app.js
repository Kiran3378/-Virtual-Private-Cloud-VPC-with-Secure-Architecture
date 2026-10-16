const routesButton = document.querySelector("#routes-toggle");
const routesLabel = document.querySelector("#routes-label");
const routeNote = document.querySelector("#route-note");
const architecture = document.querySelector(".architecture");

if (routesButton && routesLabel && routeNote && architecture) {
  routesButton.addEventListener("click", () => {
    const isShown = routesButton.getAttribute("aria-pressed") === "true";
    routesButton.setAttribute("aria-pressed", String(!isShown));
    routesLabel.textContent = isShown ? "Show traffic paths" : "Hide traffic paths";
    routeNote.hidden = isShown;
    architecture.classList.toggle("show-routes", !isShown);
  });
}

const connectButton = document.querySelector("#aws-connect");
const awsDialog = document.querySelector("#aws-dialog");
const closeDialogButton = document.querySelector("#aws-dialog-close");
const regionForm = document.querySelector("#aws-region-form");
const regionInput = document.querySelector("#aws-region");
const awsMessage = document.querySelector("#aws-message");
const awsContent = document.querySelector("#aws-content");
const connectionLabel = document.querySelector("#connection-label");
const connectionDot = document.querySelector("#connection-dot");

function showAwsMessage(message, isError = false) {
  awsMessage.textContent = message;
  awsMessage.classList.toggle("error", isError);
}

function createTextElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function fetchAwsData(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function renderVpcs(vpcs, region) {
  awsContent.replaceChildren();
  if (vpcs.length === 0) {
    showAwsMessage(`No VPCs were found in ${region}.`);
    return;
  }

  showAwsMessage(`${vpcs.length} VPC${vpcs.length === 1 ? "" : "s"} found in ${region}. Select one to inspect its network resources.`);
  const list = document.createElement("div");
  list.className = "aws-vpc-list";

  for (const vpc of vpcs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "aws-vpc-option";
    const description = document.createElement("span");
    description.append(
      createTextElement("strong", vpc.name || "Unnamed VPC"),
      createTextElement("small", `${vpc.vpc_id} · ${vpc.cidr_block} · ${vpc.state}`)
    );
    button.append(description, createTextElement("span", "View details →", "aws-option-action"));
    button.addEventListener("click", () => loadVpcDetails(vpc.vpc_id, region));
    list.append(button);
  }

  awsContent.append(list);
}

function renderInventory(data) {
  awsContent.replaceChildren();
  const backButton = createTextElement("button", "← Back to VPCs", "aws-back-button");
  backButton.type = "button";
  backButton.addEventListener("click", () => loadVpcs(data.region));
  awsContent.append(backButton);

  const heading = document.createElement("div");
  heading.className = "aws-vpc-summary";
  heading.append(
    createTextElement("strong", data.vpc.name || "Unnamed VPC"),
    createTextElement("small", `${data.vpc.vpc_id} · ${data.vpc.cidr_block} · ${data.region}`)
  );
  awsContent.append(heading);

  const permissionLines = (permissions, direction) => permissions.map(permission => {
    const protocol = permission.IpProtocol === "-1" ? "All traffic" : permission.IpProtocol || "Protocol";
    const ports = permission.FromPort === undefined ? "" :
      permission.FromPort === permission.ToPort ? `:${permission.FromPort}` : `:${permission.FromPort}-${permission.ToPort}`;
    const sources = [
      ...(permission.IpRanges || []).map(range => range.CidrIp),
      ...(permission.Ipv6Ranges || []).map(range => range.CidrIpv6),
      ...(permission.UserIdGroupPairs || []).map(pair => pair.GroupId),
      ...(permission.PrefixListIds || []).map(prefix => prefix.PrefixListId)
    ].filter(Boolean);
    return `${direction} ${protocol}${ports}${sources.length ? ` · ${sources.join(", ")}` : ""}`;
  });
  const groups = [
    ["Subnets", data.subnets, item => `${item.subnet_id} · ${item.cidr_block} · ${item.availability_zone} · ${item.map_public_ip ? "Public IP mapping" : "Private IP mapping"} · ${item.state}`, () => []],
    ["Internet gateways", data.internet_gateways, item => `${item.internet_gateway_id} · ${item.state}`, () => []],
    ["NAT gateways", data.nat_gateways, item => `${item.nat_gateway_id} · ${item.state} · ${item.subnet_id}`, () => []],
    ["Route tables", data.route_tables, item => `${item.route_table_id} · ${item.routes.length} route(s)`, item => [
      ...item.routes.map(route => `${route.destination || "No destination"} → ${route.target} · ${route.state}`),
      ...item.associations.map(subnet => `Associated subnet: ${subnet}`)
    ]],
    ["Security groups", data.security_groups, item => `${item.group_name} (${item.group_id}) · ${item.description}`, item => [
      ...permissionLines(item.ingress, "Inbound"),
      ...permissionLines(item.egress, "Outbound")
    ]],
    ["Network ACLs", data.network_acls, item => `${item.network_acl_id} · ${item.is_default ? "Default" : "Custom"}`, item =>
      item.entries.map(rule => `Rule ${rule.rule_number} · ${rule.egress ? "Outbound" : "Inbound"} · ${rule.action} · protocol ${rule.protocol} · ${rule.cidr || "No CIDR"}`)
    ],
    ["EC2 instances", data.instances, item => `${item.name || "Unnamed"} · ${item.instance_id} · ${item.state} · ${item.instance_type} · ${item.availability_zone} · ${item.private_ip || "No private IP"}`, item =>
      item.security_groups.map(group => `Security group: ${group}`)
    ],
    ["Load balancers", data.load_balancers, item => `${item.name} · ${item.type} · ${item.state} · ${item.dns_name}`, item =>
      [`Scheme: ${item.scheme}`]
    ]
  ];

  const inventory = document.createElement("div");
  inventory.className = "aws-inventory";
  for (const [title, items, describe, getDetails] of groups) {
    const section = document.createElement("section");
    section.className = "inventory-group";
    section.append(createTextElement("h3", `${title} (${items.length})`));
    if (items.length === 0) {
      section.append(createTextElement("p", "None found", "inventory-empty"));
    } else {
      const list = document.createElement("ul");
      for (const item of items) {
        const entry = document.createElement("li");
        entry.append(createTextElement("span", describe(item)));
        const details = getDetails(item);
        if (details.length > 0) {
          const detailList = document.createElement("ul");
          detailList.className = "inventory-details";
          for (const detail of details) detailList.append(createTextElement("li", detail));
          entry.append(detailList);
        }
        list.append(entry);
      }
      section.append(list);
    }
    inventory.append(section);
  }
  awsContent.append(inventory);
}

async function loadVpcs(region) {
  awsContent.replaceChildren();
  showAwsMessage(`Loading VPCs in ${region}…`);
  try {
    const data = await fetchAwsData(`/api/vpcs?region=${encodeURIComponent(region)}`);
    renderVpcs(data.vpcs, data.region);
  } catch (error) {
    showAwsMessage(error.message, true);
  }
}

async function loadVpcDetails(vpcId, region) {
  awsContent.replaceChildren();
  showAwsMessage(`Reading network resources for ${vpcId}…`);
  try {
    const data = await fetchAwsData(`/api/vpcs/${encodeURIComponent(vpcId)}?region=${encodeURIComponent(region)}`);
    renderInventory(data);
    connectionLabel.textContent = `AWS · ${data.region}`;
    connectionDot.classList.add("connected");
  } catch (error) {
    showAwsMessage(error.message, true);
  }
}

if (connectButton && awsDialog && regionForm && regionInput && awsContent && awsMessage) {
  connectButton.addEventListener("click", () => {
    awsDialog.showModal();
    regionInput.focus();
  });

  closeDialogButton.addEventListener("click", () => awsDialog.close());
  awsDialog.addEventListener("click", event => {
    if (event.target === awsDialog) awsDialog.close();
  });
  regionForm.addEventListener("submit", event => {
    event.preventDefault();
    if (!regionForm.reportValidity()) return;
    loadVpcs(regionInput.value.trim());
  });
}
