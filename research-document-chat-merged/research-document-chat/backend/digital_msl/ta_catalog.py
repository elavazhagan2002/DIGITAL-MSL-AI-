"""Standard eyecare therapeutic areas an administrator can add in one step.

They are created as local, disabled ("Coming soon") rows with a ``pending:<slug>`` placeholder id.
An administrator enables an area by linking an existing IKM repository or creating one. Each area
carries scope keywords: a question is in scope when it matches the retina vocabulary OR a keyword of
one of the doctor's assigned areas.
"""
from __future__ import annotations

import re
from typing import Any


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80]


DEFAULT_THERAPEUTIC_AREAS: tuple[dict[str, Any], ...] = (
    {
        "name": "Retina and Vitreous",
        "description": "Retinal disease, macular degeneration, retinal vein occlusion, vitreoretinal surgery and anti-VEGF therapy.",
        "keywords": ["retina", "retinal", "macular degeneration", "amd", "anti-vegf", "intravitreal", "vitreous", "vitrectomy", "rvo", "retinal detachment", "geographic atrophy"],
    },
    {
        "name": "Diabetic Retinopathy and Macular Disease",
        "description": "Diabetic retinopathy, diabetic macular edema and other macular disease.",
        "keywords": ["diabetic retinopathy", "diabetic macular edema", "dme", "dmo", "macular edema", "macular hole", "central subfield thickness", "proliferative", "npdr", "pdr"],
    },
    {
        "name": "Glaucoma",
        "description": "Open-angle and angle-closure glaucoma, intraocular pressure and glaucoma surgery.",
        "keywords": ["glaucoma", "intraocular pressure", "iop", "trabeculectomy", "optic nerve head", "visual field", "angle closure", "prostaglandin analogue", "migs"],
    },
    {
        "name": "Cornea and External Disease",
        "description": "Corneal disease, keratitis, keratoconus, corneal transplant and ocular surface infection.",
        "keywords": ["cornea", "corneal", "keratitis", "keratoconus", "keratoplasty", "corneal transplant", "conjunctivitis", "pterygium", "corneal cross-linking"],
    },
    {
        "name": "Cataract and Refractive Surgery",
        "description": "Cataract surgery, intraocular lenses, LASIK and other refractive procedures.",
        "keywords": ["cataract", "intraocular lens", "iol", "phacoemulsification", "lasik", "refractive", "myopia", "presbyopia", "astigmatism", "posterior capsule opacification"],
    },
    {
        "name": "Neuro-ophthalmology",
        "description": "Optic neuritis, optic neuropathy, visual pathway disease and ocular motility disorders.",
        "keywords": ["neuro-ophthalmology", "optic neuritis", "optic neuropathy", "papilledema", "idiopathic intracranial hypertension", "nystagmus", "visual pathway", "nmosd", "lhon"],
    },
    {
        "name": "Paediatric Ophthalmology and Strabismus",
        "description": "Childhood eye disease, amblyopia, strabismus and retinopathy of prematurity.",
        "keywords": ["paediatric", "pediatric", "amblyopia", "strabismus", "retinopathy of prematurity", "rop", "congenital cataract", "childhood myopia", "esotropia", "exotropia"],
    },
    {
        "name": "Oculoplastics and Orbit",
        "description": "Eyelid, lacrimal and orbital disease, including thyroid eye disease.",
        "keywords": ["oculoplastic", "eyelid", "ptosis", "blepharoplasty", "lacrimal", "nasolacrimal", "orbit", "orbital", "thyroid eye disease", "proptosis"],
    },
    {
        "name": "Uveitis and Ocular Inflammation",
        "description": "Anterior, intermediate, posterior and panuveitis, and immune-mediated ocular inflammation.",
        "keywords": ["uveitis", "uveitic", "ocular inflammation", "scleritis", "iritis", "vogt-koyanagi-harada", "behcet", "sarcoidosis", "adalimumab", "macular edema uveitic"],
    },
    {
        "name": "Dry Eye and Ocular Surface",
        "description": "Dry eye disease, meibomian gland dysfunction, blepharitis and ocular surface disease.",
        "keywords": ["dry eye", "keratoconjunctivitis sicca", "meibomian", "blepharitis", "ocular surface", "tear film", "cyclosporine", "lifitegrast", "schirmer", "osdi"],
    },
)
