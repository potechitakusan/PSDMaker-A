import numpy as np
import pytest

from anime_layer_agent.artist import relative_color_layers,clean_linework,configure_editing
from anime_layer_agent.compact import prepare_compact,build_compact
from anime_layer_agent.pipeline import demo
from anime_layer_agent.post_review import post_review,finish_review,CHECKS
from anime_layer_agent.selection import apply_component_assignments
from anime_layer_agent.storage import read_json,write_json,digest


def test_relative_correction_reconstructs_and_responds_to_base():
    fitted=np.array([[[.8,.4,.6],[.2,.3,.4]]],dtype=np.float32)
    target=np.array([[[.48,.32,.42],[.35,.2,.7]]])
    shadow,sa,light,ha=relative_color_layers(fitted,target)
    def apply(base):
        shaded=base*(1-sa[...,None]+shadow*sa[...,None])
        return shaded+(1-shaded)*light*ha[...,None]
    np.testing.assert_allclose(apply(fitted),target,atol=1e-6)
    assert np.all(apply(fitted*.5)<target)
    assert np.all(apply(np.minimum(fitted+.1,1))>target)


def test_line_cleanup_returns_removed_texture_to_paint_without_changing_source():
    under=np.ones((30,30,3),np.float32)
    alpha=np.zeros((30,30),np.float32)
    alpha[8:22,12]=.7
    alpha[25,25]=.4
    ink=np.zeros_like(under)
    warped=np.zeros_like(alpha);warped[8:22,12]=1
    arrays=dict(underlying=under,alpha=alpha,ink=ink,warped=warped)
    paint,cleaned,stats=clean_linework(arrays)
    assert cleaned[25,25]==0 and cleaned[15,12]==.7
    np.testing.assert_allclose(paint*(1-cleaned[...,None])+ink*cleaned[...,None],under*(1-alpha[...,None]),atol=1e-6)
    assert stats['removed_pixels']==1


def test_component_reassignment_is_local_and_rejects_source_and_id_mistakes():
    materials=np.zeros((10,10),np.int32)
    materials[1:3,1:3]=42;materials[6:8,6:8]=42
    labels=np.ones((10,10),np.int32)
    plan={'parts':[{'semantic_id':'hair'},{'semantic_id':'eye_white'}],
          'component_assignments':[{'kind':'material','region':42,'components':[2],'from':'hair','to':'eye_white'}]}
    actual=apply_component_assignments(materials,materials,labels.copy(),plan)
    assert actual[1,1]==1 and actual[6,6]==2 and np.count_nonzero(actual==2)==4
    plan['component_assignments'][0]['components']=[3]
    with pytest.raises(ValueError,match='component ID'):
        apply_component_assignments(materials,materials,labels.copy(),plan)
    plan['component_assignments'][0]['components']=[2]
    plan['component_assignments'][0]['from']='eye_white'
    with pytest.raises(ValueError,match='source mismatch'):
        apply_component_assignments(materials,materials,labels.copy(),plan)
    plan['component_assignments'][0]['from']='hair'
    plan['component_assignments']*=2
    with pytest.raises(ValueError,match='Overlapping'):
        apply_component_assignments(materials,materials,labels.copy(),plan)


@pytest.fixture
def reviewed_job(tmp_path):
    job=tmp_path/'job'
    prepare_compact(**demo(tmp_path/'input'),job=job)
    write_json(job/'semantic_plan.json',{'classification_source':'astra_reviewed','parts':[
        {'semantic_id':'background','regions':[1]}, {'semantic_id':'cloth','regions':[2],'group_path':['衣装']} ]})
    configure_editing(job)
    build_compact(job)
    return job


def fill_review(job,status='pass'):
    value=read_json(job/'post_review_assessment.json')
    for key in CHECKS:
        value['checks'][key]={'status':status,'notes':'Synthetic fixture inspection for validator test only',
                              'evidence':['post_review/overview.png']}
    write_json(job/'post_review_assessment.json',value)


def test_build_does_not_claim_visual_completion_and_pending_review_rejected(reviewed_job):
    assert read_json(reviewed_job/'status.json')['phase']=='post_review_required'
    original=digest(reviewed_job/'output.psd')
    post_review(reviewed_job)
    assert digest(reviewed_job/'output.psd')==original
    assert (reviewed_job/'post_review/recolor_sheet_01.png').exists()
    with pytest.raises(ValueError,match='Unreviewed'):
        finish_review(reviewed_job)
    fill_review(reviewed_job,'limitation')
    result=finish_review(reviewed_job)
    assert result['passed'] and len(result['limitations'])==len(CHECKS)
    assert read_json(reviewed_job/'status.json')['phase']=='complete_with_limitations'


def test_stale_plan_and_changed_review_images_cannot_pass(reviewed_job):
    post_review(reviewed_job);fill_review(reviewed_job)
    picture=reviewed_job/'post_review/overview.png'
    picture.write_bytes(picture.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='evidence changed'):
        finish_review(reviewed_job)
    plan=read_json(reviewed_job/'semantic_plan.json');plan['parts'][1]['display_name']='Changed'
    write_json(reviewed_job/'semantic_plan.json',plan)
    with pytest.raises(ValueError,match='semantic plan changed'):
        post_review(reviewed_job)


def test_failed_visual_check_is_not_complete(reviewed_job):
    post_review(reviewed_job);fill_review(reviewed_job,'fail')
    assert finish_review(reviewed_job)['passed'] is False
    assert read_json(reviewed_job/'status.json')['phase']=='needs_repair'
