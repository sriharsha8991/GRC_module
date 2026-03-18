"""Labeled evaluation dataset for RAG pipeline benchmarking.

Each sample contains:
- finding_text: a realistic security finding / audit observation
- target_frameworks: list of framework keys to search
- expected_controls: ground-truth control IDs the pipeline should map
- expected_domains: expected control domains / categories
- difficulty: easy | medium | hard
- category: thematic group (access_control, encryption, etc.)
- notes: brief rationale for the expected mapping
"""

from dataclasses import dataclass, field


@dataclass
class EvalSample:
    """A single labeled evaluation sample."""

    id: int
    finding_text: str
    target_frameworks: list[str]
    expected_controls: list[str]
    expected_domains: list[str]
    difficulty: str  # easy | medium | hard
    category: str
    notes: str = ""


# ── 50 labeled samples ──────────────────────────────────────────────────────
# Ground truth based on ISO/IEC 27001:2022 Annex A & ISO/IEC 27002:2022.
# Control IDs follow the Annex A numbering (A.5.x – A.8.x).
# ─────────────────────────────────────────────────────────────────────────────

EVAL_DATASET: list[EvalSample] = [
    # ── ACCESS CONTROL (1–8) ────────────────────────────────────────────────
    EvalSample(
        id=1,
        finding_text=(
            "Multiple user accounts with domain administrator privileges were "
            "found to have no documented business justification. Several of these "
            "accounts have not been used in over 90 days."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.15", "A.5.18", "A.8.2"],
        expected_domains=["Organizational", "Technological"],
        difficulty="easy",
        category="access_control",
        notes="A.5.15 Access control, A.5.18 Access rights, A.8.2 Privileged access rights",
    ),
    EvalSample(
        id=2,
        finding_text=(
            "The organization does not enforce multi-factor authentication for "
            "remote VPN access. Users authenticate with username and password only, "
            "creating risk of credential compromise."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.5", "A.5.17"],
        expected_domains=["Technological", "Organizational"],
        difficulty="easy",
        category="access_control",
        notes="A.8.5 Secure authentication, A.5.17 Authentication information",
    ),
    EvalSample(
        id=3,
        finding_text=(
            "The password policy allows passwords of only 6 characters with no "
            "complexity requirements. Password rotation is not enforced. There is "
            "no lockout after failed login attempts."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.17", "A.8.5"],
        expected_domains=["Organizational", "Technological"],
        difficulty="easy",
        category="access_control",
        notes="A.5.17 Authentication information, A.8.5 Secure authentication",
    ),
    EvalSample(
        id=4,
        finding_text=(
            "User access reviews are not conducted periodically. Former employees "
            "and contractors retain access to production systems up to 3 months "
            "after contract termination."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.18", "A.6.5"],
        expected_domains=["Organizational", "People"],
        difficulty="easy",
        category="access_control",
        notes="A.5.18 Access rights, A.6.5 Responsibilities after termination",
    ),
    EvalSample(
        id=5,
        finding_text=(
            "Service accounts used by automated CI/CD pipelines share the same "
            "credentials across multiple environments (dev, staging, production) "
            "and have unrestricted database write access."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.2", "A.5.15", "A.5.17"],
        expected_domains=["Technological", "Organizational"],
        difficulty="medium",
        category="access_control",
        notes="A.8.2 Privileged access rights, A.5.15 Access control, A.5.17 Auth info",
    ),
    EvalSample(
        id=6,
        finding_text=(
            "The identity management system does not enforce unique user IDs. "
            "Shared accounts are used by the operations team for server "
            "administration, making individual accountability impossible."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.16", "A.8.2"],
        expected_domains=["Organizational", "Technological"],
        difficulty="easy",
        category="access_control",
        notes="A.5.16 Identity management, A.8.2 Privileged access",
    ),
    EvalSample(
        id=7,
        finding_text=(
            "The organization lacks a formal access control policy. Access "
            "provisioning is handled ad-hoc by individual team leads without "
            "standardized procedures or approval workflows."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.15", "A.5.1"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="access_control",
        notes="A.5.15 Access control, A.5.1 Policies for information security",
    ),
    EvalSample(
        id=8,
        finding_text=(
            "Privileged access management (PAM) tools are not deployed. "
            "Root and administrator sessions are not recorded, and there is no "
            "just-in-time elevation mechanism for break-glass scenarios."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.2", "A.8.15"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="access_control",
        notes="A.8.2 Privileged access, A.8.15 Logging",
    ),

    # ── CRYPTOGRAPHY / DATA PROTECTION (9–16) ──────────────────────────────
    EvalSample(
        id=9,
        finding_text=(
            "Customer personal data is transmitted between the web application "
            "and backend API over plain HTTP. TLS is not enforced on internal "
            "service-to-service communications."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.24", "A.5.14"],
        expected_domains=["Technological", "Organizational"],
        difficulty="easy",
        category="encryption",
        notes="A.8.24 Use of cryptography, A.5.14 Information transfer",
    ),
    EvalSample(
        id=10,
        finding_text=(
            "Database backups containing PII are stored on an NFS share without "
            "encryption at rest. Backup tapes are transported offsite by a third-"
            "party courier without tamper-evident packaging."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.24", "A.8.13", "A.5.14"],
        expected_domains=["Technological", "Organizational"],
        difficulty="medium",
        category="encryption",
        notes="A.8.24 Cryptography, A.8.13 Backup, A.5.14 Information transfer",
    ),
    EvalSample(
        id=11,
        finding_text=(
            "Encryption keys for the production database are stored in a plain "
            "text configuration file on the same server. Key rotation has never "
            "been performed since the system went live 3 years ago."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.24"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="encryption",
        notes="A.8.24 Use of cryptography — covers key management",
    ),
    EvalSample(
        id=12,
        finding_text=(
            "The organization uses deprecated TLS 1.0 and SSL 3.0 cipher suites "
            "on its public-facing payment portal. Vulnerability scans have "
            "flagged POODLE and BEAST attack vectors."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.24", "A.8.8"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="encryption",
        notes="A.8.24 Cryptography, A.8.8 Management of technical vulnerabilities",
    ),
    EvalSample(
        id=13,
        finding_text=(
            "Sensitive customer data (credit card numbers, SSNs) is stored in "
            "application log files in plain text. Log files are retained for "
            "12 months with no access restrictions."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.10", "A.8.11", "A.8.15"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="data_protection",
        notes="A.8.10 Information deletion, A.8.11 Data masking, A.8.15 Logging",
    ),
    EvalSample(
        id=14,
        finding_text=(
            "Data classification policy exists but is not enforced. Developers "
            "routinely copy production data containing personal information into "
            "development and testing environments without anonymization."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.12", "A.5.13", "A.8.11", "A.8.33"],
        expected_domains=["Organizational", "Technological"],
        difficulty="medium",
        category="data_protection",
        notes=(
            "A.5.12 Classification, A.5.13 Labelling, "
            "A.8.11 Data masking, A.8.33 Test information"
        ),
    ),
    EvalSample(
        id=15,
        finding_text=(
            "The organization has no procedure for secure disposal of storage "
            "media. Decommissioned hard drives are donated to charity without "
            "being wiped or degaussed."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.7.10", "A.8.10"],
        expected_domains=["Physical", "Technological"],
        difficulty="easy",
        category="data_protection",
        notes="A.7.10 Storage media, A.8.10 Information deletion",
    ),
    EvalSample(
        id=16,
        finding_text=(
            "Data loss prevention (DLP) controls are not implemented. Large "
            "volumes of sensitive documents can be exfiltrated via USB drives, "
            "personal email, or cloud storage services without detection."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.12", "A.8.10"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="data_protection",
        notes="A.8.12 Data leakage prevention, A.8.10 Information deletion",
    ),

    # ── INCIDENT MANAGEMENT (17–22) ────────────────────────────────────────
    EvalSample(
        id=17,
        finding_text=(
            "The organization has no documented incident response plan. When a "
            "ransomware attack occurred last quarter, the response was ad-hoc "
            "and communication with stakeholders was delayed by 72 hours."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.24", "A.5.25", "A.5.26"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="incident_management",
        notes=(
            "A.5.24 Incident management planning, A.5.25 Assessment and decision, "
            "A.5.26 Response"
        ),
    ),
    EvalSample(
        id=18,
        finding_text=(
            "Security events from firewalls, IDS/IPS, and endpoint agents are "
            "not correlated centrally. There is no SIEM or equivalent system. "
            "Alert fatigue has caused genuine intrusion alerts to be missed."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.15", "A.8.16", "A.5.25"],
        expected_domains=["Technological", "Organizational"],
        difficulty="medium",
        category="incident_management",
        notes="A.8.15 Logging, A.8.16 Monitoring activities, A.5.25 Assessment and decision",
    ),
    EvalSample(
        id=19,
        finding_text=(
            "Post-incident reviews are not conducted. Lessons learned from "
            "the three security incidents this year were not documented, and "
            "no corrective actions were tracked or implemented."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.27"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="incident_management",
        notes="A.5.27 Learning from information security incidents",
    ),
    EvalSample(
        id=20,
        finding_text=(
            "Employees are not trained on how to report security incidents. "
            "The internal reporting channel is an unmonitored shared mailbox. "
            "Several phishing compromises went unreported for weeks."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.6.3", "A.5.24", "A.6.8"],
        expected_domains=["People", "Organizational"],
        difficulty="medium",
        category="incident_management",
        notes="A.6.3 Awareness/training, A.5.24 Incident management, A.6.8 Reporting",
    ),
    EvalSample(
        id=21,
        finding_text=(
            "Digital forensic evidence was not preserved during the last breach "
            "investigation. Compromised systems were rebuilt immediately, "
            "destroying volatile memory and log evidence."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.28", "A.5.26"],
        expected_domains=["Organizational"],
        difficulty="hard",
        category="incident_management",
        notes="A.5.28 Collection of evidence, A.5.26 Response to incidents",
    ),
    EvalSample(
        id=22,
        finding_text=(
            "The organization has not established communication procedures with "
            "external bodies such as law enforcement, regulators, and CERTs for "
            "incident escalation."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.5", "A.5.6"],
        expected_domains=["Organizational"],
        difficulty="medium",
        category="incident_management",
        notes="A.5.5 Contact with authorities, A.5.6 Contact with special interest groups",
    ),

    # ── NETWORK & INFRASTRUCTURE SECURITY (23–30) ──────────────────────────
    EvalSample(
        id=23,
        finding_text=(
            "The internal network is flat with no segmentation. All systems, "
            "including POS terminals, back-office servers, and guest Wi-Fi, "
            "reside on the same VLAN and subnet."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.22"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="network_security",
        notes="A.8.22 Segregation of networks",
    ),
    EvalSample(
        id=24,
        finding_text=(
            "Firewall rules have not been reviewed in over 18 months. Legacy "
            "rules permit inbound traffic on ports that are no longer required. "
            "Any-any rules exist in several rule sets."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.20", "A.8.22"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="network_security",
        notes="A.8.20 Networks security, A.8.22 Network segregation",
    ),
    EvalSample(
        id=25,
        finding_text=(
            "Web application firewalls are not deployed in front of the public-"
            "facing customer portal. The application has known SQL injection and "
            "cross-site scripting vulnerabilities from the last penetration test."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.20", "A.8.28", "A.8.8"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="network_security",
        notes="A.8.20 Network security, A.8.28 Secure coding, A.8.8 Tech vuln management",
    ),
    EvalSample(
        id=26,
        finding_text=(
            "DNS filtering and web proxy controls are not in place. Users can "
            "access known malicious domains and download executables directly "
            "from the internet without inspection."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.23", "A.8.20"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="network_security",
        notes="A.8.23 Web filtering, A.8.20 Network security",
    ),
    EvalSample(
        id=27,
        finding_text=(
            "Critical security patches for operating systems and third-party "
            "software have not been applied. The average patch lag is 120 days, "
            "and there are 45 known high-severity CVEs unpatched."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.8", "A.8.19"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="vulnerability_management",
        notes="A.8.8 Management of technical vulnerabilities, A.8.19 Installation of software",
    ),
    EvalSample(
        id=28,
        finding_text=(
            "Vulnerability scans are only performed annually. There is no "
            "continuous vulnerability management program. Authenticated scans "
            "have never been performed against the internal estate."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.8"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="vulnerability_management",
        notes="A.8.8 Management of technical vulnerabilities",
    ),
    EvalSample(
        id=29,
        finding_text=(
            "The organization uses end-of-life operating systems (Windows Server "
            "2012) for three production database servers. The vendor no longer "
            "provides security patches for these systems."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.8", "A.5.22"],
        expected_domains=["Technological", "Organizational"],
        difficulty="medium",
        category="vulnerability_management",
        notes="A.8.8 Tech vuln mgmt, A.5.22 Monitoring of agreed services (vendor)",
    ),
    EvalSample(
        id=30,
        finding_text=(
            "Wireless access points use WEP encryption. The SSID is broadcast "
            "with the default vendor configuration, and the pre-shared key has "
            "not been changed since installation."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.20", "A.8.24"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="network_security",
        notes="A.8.20 Network security, A.8.24 Cryptography",
    ),

    # ── BUSINESS CONTINUITY & BACKUP (31–35) ───────────────────────────────
    EvalSample(
        id=31,
        finding_text=(
            "Disaster recovery plans have not been tested in over two years. "
            "Recovery time objectives (RTOs) and recovery point objectives "
            "(RPOs) are not defined for critical business systems."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.29", "A.5.30"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="business_continuity",
        notes="A.5.29 ICT readiness for business continuity, A.5.30 ICT readiness for BC",
    ),
    EvalSample(
        id=32,
        finding_text=(
            "Database backups are taken weekly but have never been restore-tested. "
            "Backup logs show intermittent failures over the last year that were "
            "not investigated."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.13"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="business_continuity",
        notes="A.8.13 Information backup",
    ),
    EvalSample(
        id=33,
        finding_text=(
            "The primary data center and disaster recovery site are located in "
            "the same building. A single flooding event could render both "
            "sites inoperable simultaneously."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.14", "A.7.5"],
        expected_domains=["Technological", "Physical"],
        difficulty="medium",
        category="business_continuity",
        notes="A.8.14 Redundancy, A.7.5 Protecting against physical threats",
    ),
    EvalSample(
        id=34,
        finding_text=(
            "No redundancy exists for the single internet uplink. The "
            "organization experienced a 12-hour outage when the ISP had a "
            "regional fiber cut."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.14"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="business_continuity",
        notes="A.8.14 Redundancy of information processing facilities",
    ),
    EvalSample(
        id=35,
        finding_text=(
            "Business impact analysis has not been conducted. The organization "
            "cannot identify which systems are most critical or what the maximum "
            "tolerable downtime is for key processes."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.29", "A.5.30"],
        expected_domains=["Organizational"],
        difficulty="medium",
        category="business_continuity",
        notes="A.5.29/A.5.30 ICT readiness for business continuity",
    ),

    # ── PHYSICAL SECURITY (36–39) ──────────────────────────────────────────
    EvalSample(
        id=36,
        finding_text=(
            "The server room door is propped open during business hours. There "
            "are no badge readers, CCTV cameras, or visitor logs for the data "
            "center area."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.7.1", "A.7.2", "A.7.4"],
        expected_domains=["Physical"],
        difficulty="easy",
        category="physical_security",
        notes="A.7.1 Physical security perimeter, A.7.2 Physical entry, A.7.4 Monitoring",
    ),
    EvalSample(
        id=37,
        finding_text=(
            "UPS battery systems in the data center have not been tested under "
            "load for three years. The generator failed to start during the last "
            "power outage drill."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.7.11"],
        expected_domains=["Physical"],
        difficulty="easy",
        category="physical_security",
        notes="A.7.11 Supporting utilities",
    ),
    EvalSample(
        id=38,
        finding_text=(
            "Cabling for network and power is exposed and unlabeled in the "
            "server room. Network cables run alongside power cables without "
            "separation, and several patch cables are damaged."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.7.12"],
        expected_domains=["Physical"],
        difficulty="easy",
        category="physical_security",
        notes="A.7.12 Cabling security",
    ),
    EvalSample(
        id=39,
        finding_text=(
            "Employees are allowed to take corporate laptops home but there is "
            "no clear-desk or clear-screen policy. Sensitive documents printed "
            "at shared printers are left uncollected."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.7.7", "A.7.9", "A.7.10"],
        expected_domains=["Physical"],
        difficulty="medium",
        category="physical_security",
        notes="A.7.7 Clear desk/screen, A.7.9 Off-premises assets, A.7.10 Storage media",
    ),

    # ── SUPPLIER / THIRD-PARTY (40–42) ─────────────────────────────────────
    EvalSample(
        id=40,
        finding_text=(
            "Third-party cloud service providers have not been assessed for "
            "information security practices. SLAs do not include security "
            "requirements, and the right to audit is not contractually defined."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.19", "A.5.20", "A.5.21"],
        expected_domains=["Organizational"],
        difficulty="medium",
        category="supplier_management",
        notes="A.5.19-21 Supplier relationship security, agreements, ICT supply chain",
    ),
    EvalSample(
        id=41,
        finding_text=(
            "The organization outsources payroll processing but has not verified "
            "whether the payroll vendor has ISO 27001 certification or equivalent "
            "security controls. No security clauses exist in the contract."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.19", "A.5.20"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="supplier_management",
        notes="A.5.19 Information security in supplier relationships, A.5.20 Agreements",
    ),
    EvalSample(
        id=42,
        finding_text=(
            "A critical SaaS vendor suffered a data breach affecting customer "
            "data, but the organization was not notified for 30 days. There are "
            "no contractual breach notification requirements."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.22", "A.5.20", "A.5.24"],
        expected_domains=["Organizational"],
        difficulty="hard",
        category="supplier_management",
        notes="A.5.22 Monitoring, A.5.20 Agreements, A.5.24 Incident management",
    ),

    # ── CHANGE MANAGEMENT / SDLC (43–46) ──────────────────────────────────
    EvalSample(
        id=43,
        finding_text=(
            "Changes to production systems are deployed directly without formal "
            "change advisory board approval. There is no change management "
            "process or back-out plan documented."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.32"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="change_management",
        notes="A.8.32 Change management",
    ),
    EvalSample(
        id=44,
        finding_text=(
            "The software development team does not perform security code reviews "
            "or static application security testing (SAST) before releases. "
            "OWASP Top 10 vulnerabilities have been found in production code."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.28", "A.8.29"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="change_management",
        notes="A.8.28 Secure coding, A.8.29 Security testing in development",
    ),
    EvalSample(
        id=45,
        finding_text=(
            "Development, testing, and production environments are not separated. "
            "Developers have write access to the production database and can "
            "deploy code without going through the release pipeline."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.31", "A.8.32"],
        expected_domains=["Technological"],
        difficulty="easy",
        category="change_management",
        notes="A.8.31 Separation of environments, A.8.32 Change management",
    ),
    EvalSample(
        id=46,
        finding_text=(
            "The application uses open-source libraries with known critical CVEs "
            "including Log4Shell. Software composition analysis is not part of "
            "the CI/CD pipeline."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.8.28", "A.8.8"],
        expected_domains=["Technological"],
        difficulty="medium",
        category="change_management",
        notes="A.8.28 Secure coding, A.8.8 Technical vulnerability management",
    ),

    # ── GOVERNANCE & AWARENESS (47–50) ─────────────────────────────────────
    EvalSample(
        id=47,
        finding_text=(
            "The information security policy was last updated in 2019 and does "
            "not reflect current threats such as ransomware, supply chain attacks, "
            "or remote work risks."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.1"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="governance",
        notes="A.5.1 Policies for information security",
    ),
    EvalSample(
        id=48,
        finding_text=(
            "Information security roles and responsibilities are not formally "
            "defined. There is no CISO or equivalent role. The IT manager "
            "handles security as an ad-hoc secondary responsibility."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.2", "A.5.4"],
        expected_domains=["Organizational"],
        difficulty="easy",
        category="governance",
        notes="A.5.2 Roles and responsibilities, A.5.4 Management responsibilities",
    ),
    EvalSample(
        id=49,
        finding_text=(
            "New employees do not receive security awareness training during "
            "onboarding. Annual refresher training is not delivered. Phishing "
            "simulation results show a 40% click-through rate."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.6.3", "A.6.6"],
        expected_domains=["People"],
        difficulty="easy",
        category="awareness",
        notes="A.6.3 Information security awareness/training, A.6.6 Confidentiality agreements",
    ),
    EvalSample(
        id=50,
        finding_text=(
            "Risk assessments are conducted only during ISO certification audits. "
            "There is no continuous risk register, and threat intelligence is not "
            "used to inform the assessment of new risks."
        ),
        target_frameworks=["iso_27001"],
        expected_controls=["A.5.7", "A.5.3"],
        expected_domains=["Organizational"],
        difficulty="hard",
        category="governance",
        notes="A.5.7 Threat intelligence, A.5.3 Segregation of duties (risk context)",
    ),
]


def get_dataset() -> list[EvalSample]:
    """Return the full evaluation dataset."""
    return EVAL_DATASET


def get_dataset_by_category(category: str) -> list[EvalSample]:
    """Filter dataset by category."""
    return [s for s in EVAL_DATASET if s.category == category]


def get_dataset_by_difficulty(difficulty: str) -> list[EvalSample]:
    """Filter dataset by difficulty."""
    return [s for s in EVAL_DATASET if s.difficulty == difficulty]


def dataset_summary() -> dict:
    """Return summary statistics about the dataset."""
    categories: dict[str, int] = {}
    difficulties: dict[str, int] = {}
    for s in EVAL_DATASET:
        categories[s.category] = categories.get(s.category, 0) + 1
        difficulties[s.difficulty] = difficulties.get(s.difficulty, 0) + 1
    return {
        "total_samples": len(EVAL_DATASET),
        "categories": categories,
        "difficulties": difficulties,
        "frameworks": list({fw for s in EVAL_DATASET for fw in s.target_frameworks}),
    }
