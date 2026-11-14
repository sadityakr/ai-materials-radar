"""Cheap keyword pre-filter. Deliberately loose: it only removes postings that are clearly
not AI x materials/process roles. The final call is made by a Haiku classifier later."""
import re

TITLE_INCLUDE = re.compile(
    r"(data|machine.learning|\bml\b|deep.learning|artificial|intelligen|\bAI\b|\bKI\b|analytic|statistic|scien|computational|"
    r"simulation|model|informatic|digital|automation|algorithm|python|vision|image|metrolog|messtechnik|inspection|yield|"
    r"process|prozess|material|werkstoff|physic|physik|research|r&d|r ?& ?d|forschung|graduate|trainee|postdoc|post-doc|"
    r"chemist|chemiker|application|applikation|characteri|spectroscop|microscop|lithograph|epitax|deposition|etch|thin.film|"
    r"semiconductor|halbleiter|wafer|device|integration|quantum|photonic|battery|batterie|catalys|polymer|alloy|crystal|"
    r"apc|fdc|\bspc\b|smart.manufactur|industr(y|ie) ?4|"
    r"数据|算法|人工智能|机器学习|工艺|材料|量测|检测|仿真|研发)", re.I)

TITLE_EXCLUDE = re.compile(
    r"(\bintern\b|internship|praktik|werkstudent|working.student|student.assistant|studentische|hiwi|apprentic|ausbildung|"
    r"azubi|duales.studium|dual.stud|thesis|abschlussarbeit|masterarbeit|bachelorarbeit|bachelor.?arbeit|doktorand|"
    r"phd.(student|position|candidate|researcher)|promotionsstelle|stage\b|alternance|stagiaire|实习|"
    r"\bsales\b|account.manager|key.account|vertrieb|marketing|finance|accountant|controller|controlling|buchhalt|payroll|"
    r"\bhr\b|human.resources|recruit|talent.acq|legal|lawyer|jurist|procurement|einkauf|buyer|purchas|logistic|warehouse|"
    r"\blager|facility|facilities|hausmeister|cleaning|reinigung|security.guard|driver|fahrer|electrician|elektriker|"
    r"mechatroni|technician|techniker|operator|bediener|produktionsmitarbeiter|assembly|montage|maintenance|instandhaltung|"
    r"customer.service|kundenservice|helpdesk|it.support|service.desk|\bsap\b|\berp\b|executive.assistant|assistenz|"
    r"sekret|office.manager|reception|director|vice.president|\bvp\b|head.of|chief|general.manager|"
    r"front.?end|back.?end|full.?stack|\bios\b|android|devops|\bsre\b|site.reliability|cloud.engineer|security.engineer|"
    r"cyber|penetration|it.administrator|systemadministrator)", re.I)

AI_TERMS = re.compile(
    r"(machine.learning|deep.learning|artificial.intelligence|\bAI\b|\bKI\b|künstliche.intelligenz|data.scien|"
    r"data.analy|data.engineer|analytics|statistic|python|neural|computer.vision|image.(analysis|processing)|bayesian|"
    r"optimi[sz]ation|modell?ing|simulation|computational|informatics|digital.twin|automation|algorithm|\bLLM|predictive|"
    r"big.data|smart.manufactur|industr(y|ie).?4|advanced.process.control|\bAPC\b|\bFDC\b|\bSPC\b|run.to.run|virtual.metrology|"
    r"数据|算法|人工智能|机器学习)", re.I)

MATPROC_TERMS = re.compile(
    r"(material|werkstoff|process|prozess|\bfab\b|wafer|semiconductor|halbleiter|metrolog|messtechnik|inspection|thin.film|"
    r"deposition|etch|lithograph|epitax|\bCMP\b|chemi|battery|batterie|catalys|polymer|alloy|crystal|spectroscop|microscop|"
    r"characteri|physic|physik|graphene|2D|photonic|manufactur|fertigung|yield|defect|sensor|magnet|\bDFT\b|molecular|quantum|"
    r"工艺|材料|量测|检测|半导体)", re.I)


def title_pass(title):
    return bool(TITLE_INCLUDE.search(title or "")) and not TITLE_EXCLUDE.search(title or "")


def desc_pass(title, desc):
    """True if both an AI/data signal and a materials/process signal appear. If no description, defer (True)."""
    if not desc:
        return True
    text = f"{title} {desc}"
    return bool(AI_TERMS.search(text)) and bool(MATPROC_TERMS.search(text))
